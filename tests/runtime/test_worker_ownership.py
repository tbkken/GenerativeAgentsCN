"""Startup fencing, adoption and execution slots are shared across Studio processes."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from filelock import FileLock

from generative_agents.ga_protocol.packages.io import PackageError, atomic_write_json, read_json
from generative_agents.ga_runtime.api import FileRunSupervisor, RunService, reconcile_run
from generative_agents.ga_runtime.supervision import ownership
from tests.test_portable_package_protocol import _experiment


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setattr(ownership.tempfile, "gettempdir", lambda: str(host))
    source = _experiment(tmp_path / "experiment")
    service = RunService()
    return [service.create(source, tmp_path / "runs" / key) for key in ("one", "two")]


def test_queue_fences_late_child_watcher_and_concurrent_submit(workspace):
    first, second = workspace
    with ownership.submit_launch(first, 1) as old_token:
        pass
    assert reconcile_run(first).status.value == "QUEUED"
    assert ownership.process_observation(first)["liveness"] == "starting"
    with pytest.raises(RuntimeError, match="slot"):
        with ownership.submit_launch(second, 1):
            pytest.fail("slot should be reserved while the child starts")
    with pytest.raises(PackageError, match="另一执行进程"):
        with ownership.worker_execution(first):
            pytest.fail("a CLI must not take a live launch reservation")

    path = ownership._record_path(first)
    record = read_json(path)
    record["deadline"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    atomic_write_json(path, record)
    with pytest.raises(PackageError, match="启动令牌"):
        with ownership.worker_execution(first, launch_token=old_token):
            pytest.fail("an expired child cannot execute, even when nobody has probed it")
    assert reconcile_run(first).status.value == "FAILED"
    with ownership.submit_launch(first, 1) as new_token:
        pass
    assert new_token != old_token
    assert reconcile_run(first, expected_token=old_token).status.value == "QUEUED"
    with pytest.raises(PackageError, match="启动令牌"):
        with ownership.worker_execution(first, launch_token=old_token):
            pytest.fail("only the new token may acquire this launch")


def test_live_worker_blocks_resume_and_occupies_slot_after_web_restarts(workspace, monkeypatch):
    first, second = workspace
    with ownership.submit_launch(first, 1) as token:
        pass
    monkeypatch.setattr(ownership, "HEARTBEAT_SECONDS", 600)
    with ownership.worker_execution(first, launch_token=token):
        status = read_json(first / "status.json")
        status["status"] = "RUNNING"
        atomic_write_json(first / "status.json", status)
        path = ownership._record_path(first)
        record = read_json(path)
        record["heartbeat_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        atomic_write_json(path, record)
        observation = FileRunSupervisor(max_concurrent_runs=1).process_status(status["run_id"], first)
        assert observation["liveness"] == "running" and observation["heartbeat_stale"]
        assert not observation["owned_by_this_studio"] and observation["process_id"] == os.getpid()
        assert reconcile_run(first).status.value == "RUNNING"
        with pytest.raises(RuntimeError, match="slot"):
            with ownership.submit_launch(second, 1):
                pytest.fail("a new Studio must count the existing execution")
    assert reconcile_run(first).status.value == "FAILED"
    assert not path.exists()
    with ownership.submit_launch(second, 1):
        pass


def test_external_worker_without_host_record_is_counted(workspace):
    first, second = workspace
    status = read_json(first / "status.json")
    status["status"] = "RUNNING"
    atomic_write_json(first / "status.json", status)
    with FileLock(str(first / "worker.lock")):
        with pytest.raises(RuntimeError, match="slot"):
            with ownership.submit_launch(second, 1):
                pytest.fail("an external execution must occupy a slot")
        assert reconcile_run(first).status.value == "RUNNING"
        assert ownership.process_observation(first)["liveness"] == "unknown"
    assert reconcile_run(first).status.value == "FAILED"


def test_native_process_exit_releases_lock_and_reconciles_on_next_observation(workspace, monkeypatch):
    first, _ = workspace
    # The spawned interpreter uses the same host scratch directory as this test.
    monkeypatch.setenv("TEMP", str(ownership.tempfile.gettempdir()))
    monkeypatch.setenv("TMP", str(ownership.tempfile.gettempdir()))
    monkeypatch.setenv("TMPDIR", str(ownership.tempfile.gettempdir()))
    with ownership.submit_launch(first, 1) as token:
        pass
    script = """
import os, sys
from pathlib import Path
from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_runtime.supervision.ownership import worker_execution
root = Path(sys.argv[1])
with worker_execution(root, launch_token=sys.argv[2]):
    status = read_json(root / 'status.json')
    status['status'] = 'RUNNING'
    atomic_write_json(root / 'status.json', status)
    os._exit(7)
"""
    process = subprocess.run([sys.executable, "-B", "-c", script, str(first), token],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    assert process.returncode == 7, process.stdout
    settled = reconcile_run(first)
    assert settled.status.value == "FAILED" and settled.committed_step == 0
    assert reconcile_run(first) == settled
