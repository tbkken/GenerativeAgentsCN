"""Coordinate local workers across Studio restarts without changing portable packages."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from generative_agents.ga_protocol.packages.io import PackageError, atomic_write_json, checked_package_path, read_json
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.schemas.manifests import RunState, RunStatus
from generative_agents.ga_runtime.lifecycle.control import FileRunControl


ACTIVE = {RunState.QUEUED, RunState.RUNNING, RunState.FINALIZING}
LAUNCH_ENV = "GA_RUNTIME_LAUNCH_TOKEN"
STARTUP_GRACE = timedelta(seconds=60)
HEARTBEAT_SECONDS = 5
HEARTBEAT_STALE_SECONDS = 25
_LOG = logging.getLogger(__name__)


def _key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(os.path.normcase(str(path)))).hexdigest()


def _record_path(root: Path) -> Path:
    # One execution pool per local Run workspace. Host identities and leases
    # are intentionally outside Run directories and all portable exports.
    return Path(tempfile.gettempdir()) / "ga-runtime-supervision" / _key(root.parent) / f"{_key(root)}.json"


def _status(root: Path) -> RunStatus:
    status = RunStatus.model_validate(read_json(root / "status.json"))
    if status.run_id != read_json(root / "run.json")["run_id"]:
        raise PackageError("Run status belongs to another Run")
    return status


def _record(path: Path, run_id: str) -> dict | None:
    if path.is_file():
        record = read_json(path)
        if record["run_id"] == run_id:
            return record
    return None


def _worker_lock(root: Path) -> FileLock:
    return FileLock(str(checked_package_path(root / "worker.lock")), timeout=0)


def _pending(record: dict | None, status: RunStatus) -> bool:
    if status.status != RunState.QUEUED:
        return False
    if record is None:
        # A worker launched by an older Studio may still be loading.
        return datetime.now(UTC) - status.updated_at < STARTUP_GRACE
    return record["phase"] == "starting" and datetime.now(UTC) < datetime.fromisoformat(record["deadline"])


def _settle(root: Path, reason: str) -> RunStatus:
    from generative_agents.ga_runtime.lifecycle.executor import settle_interrupted_run_locked

    return settle_interrupted_run_locked(root, reason)


def _unfinished_attempt(root: Path, status: RunStatus) -> bool:
    # An older worker can die between publishing the Run and Attempt records
    # without leaving a host lease. Read only the active Attempt in this path.
    if status.active_attempt_id is not None:
        attempt = root / "attempts" / status.active_attempt_id / "attempt.json"
        return attempt.is_file() and read_json(attempt).get("status") == "RUNNING"
    return status.status == RunState.CREATED and any((root / "attempts").glob("*/attempt.json"))


def _remember_external_worker(root: Path, status: RunStatus) -> None:
    # Only a held OS lock proves occupancy. The PID of an existing worker
    # started without host metadata is unknown, including after a Web upgrade.
    record = _new_record(root, status, None)
    record["phase"] = "external"
    atomic_write_json(_record_path(root), record)


def reconcile_run(run_directory: str | Path, *, expected_token: str | None = None, reason: str | None = None) -> RunStatus:
    """Reconcile under the OS worker lock. A late watcher can only settle its own launch."""
    root = checked_package_path(Path(run_directory))
    path = _record_path(root)
    with ExitStack() as execution:
        with package_lock(path.parent):
            status = _status(root)
            record = _record(path, status.run_id)
            if expected_token is not None and (not record or record["token"] != expected_token):
                return status
            if status.status not in ACTIVE and record is None and not _unfinished_attempt(root, status):
                return status
            try:
                execution.enter_context(_worker_lock(root))
            except Timeout:
                if record is None and status.status in ACTIVE:
                    _remember_external_worker(root, status)
                return _status(root)
            except OSError:
                # If permissions or the filesystem prevent a probe, there is
                # no proof the writer has exited.
                return status
            status = _status(root)
            if expected_token is None and _pending(record, status):
                return status
        # Only this Run is blocked by expensive evidence verification.
        status = _settle(root, reason or "仿真执行进程已退出，已释放执行锁。")
        with package_lock(path.parent):
            path.unlink(missing_ok=True)
        return status


def process_observation(root: Path) -> dict:
    """Cheap diagnostics; heartbeat age is independent of Step progress and never fences a writer."""
    root = checked_package_path(root)
    path = _record_path(root)
    with package_lock(path.parent):
        status = _status(root)
        record = _record(path, status.run_id) or {}
        try:
            with _worker_lock(root):
                live = "starting" if _pending(record or None, status) else "inactive"
        except Timeout:
            live = "running" if record.get("phase") == "running" else "unknown"
        except OSError:
            live = "unknown"
        heartbeat = record.get("heartbeat_at")
        stale = bool(heartbeat and (datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds() > HEARTBEAT_STALE_SECONDS)
        return {"liveness": live, "process_id": record.get("pid"), "heartbeat_at": heartbeat,
                "heartbeat_stale": stale, "progress_at": status.updated_at.isoformat()}


def _reserve_slot(root: Path, limit: int) -> None:
    # Caller holds the workspace's dispatch lock. Dead slots are immediately
    # reusable; their records remain available for crash diagnosis/reconciliation.
    live = 0
    path = _record_path(root)
    # An already-running worker may predate host records. Also count older
    # launches during their bounded startup window.
    for manifest in root.parent.glob("*/run.json"):
        sibling = manifest.parent
        if sibling == root or _record_path(sibling).is_file() or not (sibling / "status.json").is_file():
            continue
        try:
            status = _status(sibling)
        except (OSError, ValueError, KeyError):
            continue
        if status.status not in ACTIVE:
            continue
        try:
            with _worker_lock(sibling):
                if _pending(None, status):
                    live += 1
        except Timeout:
            _remember_external_worker(sibling, status)
    for item in path.parent.glob("*.json"):
        if item == path:
            continue
        record = read_json(item)
        assigned = checked_package_path(Path(record["root"]))
        if _record_path(assigned) != item:
            raise PackageError("invalid host execution slot identity")
        if not (assigned / "run.json").is_file():
            item.unlink(missing_ok=True)
            continue
        try:
            with _worker_lock(assigned):
                status = _status(assigned)
                occupied = status.run_id == record["run_id"] and _pending(record, status)
        except Timeout:
            occupied = True
        if occupied:
            live += 1
            if record["slot_limit"] is not None:
                limit = min(limit, record["slot_limit"])
    if live >= limit:
        raise RuntimeError("no local Runtime execution slot is available")


def _new_record(root: Path, status: RunStatus, limit: int | None) -> dict:
    return {"root": str(root), "run_id": status.run_id, "token": str(uuid4()),
            "phase": "starting", "slot_limit": limit, "pid": None, "heartbeat_at": None,
            "deadline": (datetime.now(UTC) + STARTUP_GRACE).isoformat()}


@contextmanager
def submit_launch(root: Path, limit: int):
    """Publish QUEUED plus a one-use token before spawning, or roll back the submit."""
    root = checked_package_path(root)
    path = _record_path(root)
    with package_lock(path.parent), _worker_lock(root):
        status = _status(root)
        if status.status not in {RunState.CREATED, RunState.PAUSED, RunState.FAILED}:
            raise PackageError(f"Run cannot be submitted from {status.status.value}")
        _reserve_slot(root, limit)
        control = FileRunControl(root)
        if status.status in {RunState.PAUSED, RunState.FAILED}:
            control.reset()
        previous = status.model_copy()
        record = _new_record(root, status, limit)
        atomic_write_json(path, record)
        try:
            status.status = RunState.QUEUED
            status.reason = None
            status.updated_at = datetime.now(UTC)
            atomic_write_json(root / "status.json", status.model_dump(mode="json"))
            yield record["token"]
        except BaseException:
            atomic_write_json(root / "status.json", previous.model_dump(mode="json"))
            path.unlink(missing_ok=True)
            raise


def _heartbeat(path: Path, record: dict, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        record["heartbeat_at"] = datetime.now(UTC).isoformat()
        try:
            atomic_write_json(path, record)
        except OSError:
            _LOG.exception("Cannot publish worker heartbeat for run=%s", record["run_id"])


@contextmanager
def worker_execution(run_directory: Path, *, launch_token: str | None = None):
    """Claim a launch and hold the OS lock continuously through the last state write."""
    root = checked_package_path(run_directory)
    path = _record_path(root)
    with ExitStack() as execution:
        with package_lock(path.parent):
            execution.enter_context(_worker_lock(root))
            status = _status(root)
            record = _record(path, status.run_id)
            if launch_token is not None:
                if not record or record["token"] != launch_token or not _pending(record, status):
                    raise PackageError("仿真启动令牌已过期或已被替换。")
            else:
                queued_cancel = record is None and status.status == RunState.QUEUED and FileRunControl(root).cancel_requested
                if _pending(record, status) and not queued_cancel:
                    raise PackageError("仿真正由另一执行进程启动。")
                _reserve_slot(root, 2)
                record = _new_record(root, status, 2)
            record.update(phase="running", pid=os.getpid(), heartbeat_at=datetime.now(UTC).isoformat())
            atomic_write_json(path, record)
        stop = threading.Event()
        thread = threading.Thread(target=_heartbeat, args=(path, record, stop), name="ga-worker-heartbeat", daemon=True)
        thread.start()
        succeeded = False
        try:
            if launch_token is None and status.status in ACTIVE and not queued_cancel:
                _settle(root, "仿真执行进程已退出，已释放执行锁。")
            yield
            succeeded = True
        finally:
            stop.set()
            thread.join()
            # Leave the claim for a watcher if preparation aborted before the
            # worker could publish a terminal outcome. A killed process leaves
            # it too. Both paths are fenced by the same token and worker lock.
            if succeeded and _status(root).status not in ACTIVE:
                with package_lock(path.parent):
                    path.unlink(missing_ok=True)
