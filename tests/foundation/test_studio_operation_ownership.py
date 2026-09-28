"""A second Web worker cannot recover or start another live worker's jobs."""
import multiprocessing
from threading import Event

from generative_agents.ga_studio.operations import OperationJobs


def _worker(directory, ready, finish):
    jobs = OperationJobs(directory)
    running = Event()
    def work():
        running.set()
        if not finish.wait(20):
            raise RuntimeError("test completion signal timed out")
        return {"complete": True}
    try:
        item = jobs.submit("TEST", work)
        if not running.wait(10):
            raise RuntimeError("test operation did not start")
        ready.send(item["operation_id"])
        finish.wait(20)
    finally:
        jobs.close()


def _start_worker(tmp_path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    finish = context.Event()
    process = context.Process(target=_worker, args=(str(tmp_path), child, finish))
    process.start()
    child.close()
    assert parent.poll(15), "worker did not publish its running operation"
    identity = parent.recv()
    parent.close()
    return process, finish, identity


def _stop(process, finish):
    if process.is_alive():
        finish.set()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)


def test_second_process_keeps_live_owner_running(tmp_path):
    process, finish, identity = _start_worker(tmp_path)
    observer = None
    try:
        observer = OperationJobs(tmp_path)
        assert observer.get(identity)["status"] == "RUNNING"
        _stop(process, finish)
        assert process.exitcode == 0
        assert observer.get(identity)["status"] == "SUCCEEDED"
    finally:
        _stop(process, finish)
        if observer:
            observer.close()


def test_crashed_owner_is_recovered_after_os_releases_lease(tmp_path):
    process, finish, identity = _start_worker(tmp_path)
    survivor = OperationJobs(tmp_path)
    try:
        assert survivor.get(identity)["status"] == "RUNNING"
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        assert survivor.get(identity)["status"] == "FAILED"
        recovered = OperationJobs(tmp_path)
        try:
            item = recovered.get(identity)
            assert item["status"] == "FAILED"
            assert "服务重启中断" in item["error"]
        finally:
            recovered.close()
    finally:
        _stop(process, finish)
        survivor.close()


def test_other_manager_can_cancel_queued_work_before_it_starts(tmp_path):
    first = OperationJobs(tmp_path)
    second = None
    gate, executed = Event(), Event()
    started = [Event(), Event()]
    def occupy(index):
        started[index].set()
        gate.wait(10)
    try:
        for index in range(2):
            first.submit("BLOCKING", lambda index=index: occupy(index))
        assert all(item.wait(5) for item in started)
        queued = first.submit("CANCEL", lambda: executed.set())
        second = OperationJobs(tmp_path)
        assert second.get(queued["operation_id"])["status"] == "QUEUED"
        assert second.cancel(queued["operation_id"])["status"] == "CANCELLED"
        gate.set()
        first.close()
        assert not executed.is_set()
        assert second.get(queued["operation_id"])["status"] == "CANCELLED"
    finally:
        gate.set()
        first.close()
        if second:
            second.close()
