"""Kill a real worker at the file publication boundaries and recover the same Run."""
import multiprocessing
from pathlib import Path
import time

import pytest
from filelock import FileLock

from generative_agents.ga_protocol.packages.io import PackageError, atomic_write_json, read_json
from generative_agents.ga_runtime.lifecycle import executor
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.test_portable_package_protocol import _experiment


def _hold_worker(root, point, ready):
    write = executor.atomic_write_json

    def publish(path, value):
        is_status = path.name == "status.json"
        before = (is_status and value["status"] == "RUNNING"
                  and value["committed_step"] == 2 and point == "unpublished-step")
        if not before:
            write(path, value)
        reached = (
            before
            or (point == "unpublished-attempt" and path.name == "attempt.json")
            or (point == "initial" and is_status and value["status"] == "RUNNING"
                and value["committed_step"] == 0)
            or (point == "finalizing" and is_status and value["status"] == "FINALIZING")
            or (point == "report" and path.name == "quality-report.json")
        )
        if reached:
            ready.send(True)
            time.sleep(45)
        if before:
            write(path, value)

    executor.atomic_write_json = publish
    executor.execute_run_directory(Path(root))


def _kill_worker(root, point):
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_hold_worker, args=(str(root), point, sender))
    worker.start()
    sender.close()
    try:
        assert receiver.poll(30) and receiver.recv(), f"worker missed {point}"
        worker.terminate()
        worker.join(15)
        assert worker.exitcode not in (0, None)
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(10)
        receiver.close()
        worker.close()


def _facts(root, boundary):
    return {relative: (root / relative).read_bytes()
            for step in range(1, boundary + 1)
            for relative in (f"frames/step-{step:06d}.json.gz", f"commits/step-{step:06d}.json")}


@pytest.mark.parametrize(("point", "boundary", "attempt_count"), [
    ("unpublished-attempt", 0, 2), ("initial", 0, 2), ("unpublished-step", 1, 2),
    ("finalizing", 3, 1), ("report", 3, 1),
])
def test_killed_worker_recovers_exact_boundary(tmp_path, point, boundary, attempt_count, monkeypatch):
    root = RunService().create(_experiment(tmp_path), tmp_path / "run", requested_steps=3)
    _kill_worker(root, point)
    stale = read_json(root / "status.json")
    assert stale["status"] in {"CREATED", "RUNNING", "FINALIZING"} and stale["committed_step"] == boundary
    interrupted = next((root / "attempts").glob("*/attempt.json")).parent.name
    before = _facts(root, boundary)
    report_path = root / "artifacts/quality-report.json"
    published_report = report_path.read_bytes() if point == "report" else None
    if point == "unpublished-step":
        assert (root / "commits/step-000002.json").exists()
    if boundary == stale["total_steps"]:
        def forbidden(*args, **kwargs):
            pytest.fail("finalization started another simulation")
        monkeypatch.setattr(executor, "build_runner", forbidden)
    # Either a Studio restart reconciles first, or a CLI user resumes directly.
    if point != "unpublished-step":
        with FileLock(str(root / "worker.lock"), timeout=0):
            stopped = executor.settle_interrupted_run_locked(root, "测试进程已退出")
        assert stopped.status.value == "FAILED" and stopped.committed_step == boundary
    finished = RunService().resume(root)
    assert finished.run_id == stale["run_id"] and finished.status.value == "COMPLETED"
    assert _facts(root, boundary) == before
    attempts = sorted((read_json(path) for path in (root / "attempts").glob("*/attempt.json")),
                      key=lambda item: item["ordinal"])
    assert len(attempts) == attempt_count
    assert attempts[0]["attempt_id"] == interrupted
    assert attempts[0]["status"] == "FAILED" and attempts[0]["finished_at"] and attempts[0]["failure"]
    if len(attempts) == 2:
        assert attempts[1]["resumed_from_step"] == boundary and attempts[1]["status"] == "COMPLETED"
        assert attempts[1]["attempt_id"] == finished.active_attempt_id
    if point == "unpublished-step":
        assert list((root / "orphaned").rglob("step-000002.json.gz"))
    report = read_json(report_path)
    assert report["source_run_id"] == finished.run_id and report["source_committed_step"] == 3
    if published_report:
        assert report_path.read_bytes() == published_report


def test_corrupt_committed_frame_blocks_finalization_and_reports_why(tmp_path):
    root = RunService().create(_experiment(tmp_path), tmp_path / "run", requested_steps=3)
    _kill_worker(root, "finalizing")
    frame = root / "frames/step-000001.json.gz"
    frame.write_bytes(b"bad frame")
    with FileLock(str(root / "worker.lock"), timeout=0):
        status = executor.settle_interrupted_run_locked(root, "测试进程已退出")
    assert status.status.value == "FAILED" and "hash mismatch" in status.reason
    with pytest.raises(PackageError, match="hash mismatch"):
        RunService().resume(root)
    assert len(list((root / "attempts").glob("*/attempt.json"))) == 1
    assert frame.read_bytes() == b"bad frame"
    assert not (root / "artifacts/quality-report.json").exists()


@pytest.mark.parametrize("damaged_copy", ["recovery", "checkpoints"])
def test_resume_uses_valid_copy_of_the_same_boundary(tmp_path, damaged_copy):
    root = RunService().create(_experiment(tmp_path), tmp_path / "run", requested_steps=3)
    _kill_worker(root, "unpublished-step")
    before = _facts(root, 1)
    (root / damaged_copy / "step-000001/state.json").write_bytes(b"{}")
    finished = RunService().resume(root)
    assert finished.status.value == "COMPLETED" and _facts(root, 1) == before


def test_reconciliation_finishes_dangling_attempt_of_completed_run(tmp_path):
    root = tmp_path / "run"
    completed = RunService().start(_experiment(tmp_path), root, requested_steps=1)
    path = root / "attempts" / completed.active_attempt_id / "attempt.json"
    record = read_json(path)
    record.update(status="RUNNING", finished_at=None)
    atomic_write_json(path, record)
    before = (root / "status.json").read_bytes()
    with FileLock(str(root / "worker.lock"), timeout=0):
        executor.settle_interrupted_run_locked(root, "测试进程已退出")
        attempt = path.read_bytes()
        executor.settle_interrupted_run_locked(root, "再次核对")
    assert read_json(path)["status"] == "COMPLETED" and read_json(path)["finished_at"]
    assert path.read_bytes() == attempt and (root / "status.json").read_bytes() == before


@pytest.mark.parametrize(("run_state", "attempt_state", "expected"), [
    ("RUNNING", "PAUSED", "PAUSED"), ("RUNNING", "CANCELLED", "CANCELLED"),
    ("RUNNING", "FAILED", "FAILED"), ("QUEUED", "PAUSED", "FAILED"),
])
def test_published_outcome_only_belongs_to_current_worker(tmp_path, run_state, attempt_state, expected):
    root = tmp_path / "run"
    RunService().start(_experiment(tmp_path), root, requested_steps=1)
    status = read_json(root / "status.json")
    path = root / "attempts" / status["active_attempt_id"] / "attempt.json"
    attempt = read_json(path)
    attempt.update(status=attempt_state, failure="原始故障" if attempt_state == "FAILED" else None)
    atomic_write_json(path, attempt)
    status.update(status=run_state, committed_step=0)
    atomic_write_json(root / "status.json", status)
    with FileLock(str(root / "worker.lock"), timeout=0):
        stopped = executor.settle_interrupted_run_locked(root, "worker stopped")
    assert stopped.status.value == expected
    if attempt_state == "FAILED":
        assert stopped.reason == "原始故障"
    assert read_json(path) == attempt
