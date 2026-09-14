"""Local file-only process supervision for portable Run directories."""

from __future__ import annotations

import subprocess
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from generative_agents.ga_protocol import (
    PackageError,
    RunState,
    RunStatus,
    atomic_write_json,
    read_json,
    validate_run_directory,
)


class FileRunSupervisor:
    """Own execution slots and child processes without a scheduler database."""

    def __init__(
        self,
        *,
        max_concurrent_runs: int = 2,
        on_complete: Callable[[Path], None] | None = None,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be positive")
        self._slots = threading.BoundedSemaphore(max_concurrent_runs)
        self._on_complete = on_complete
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def submit(self, run_directory: str | Path, *, environment: dict[str, str] | None = None) -> str:
        run_root = Path(run_directory).resolve()
        manifest = validate_run_directory(run_root)
        status_path = run_root / "status.json"
        status = RunStatus.model_validate(read_json(status_path))
        if status.status not in {RunState.CREATED, RunState.PAUSED, RunState.FAILED}:
            raise PackageError(f"Run cannot be submitted from {status.status.value}")
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("no local Runtime execution slot is available")
        status.status = RunState.QUEUED
        status.reason = None
        status.updated_at = datetime.now(UTC)
        atomic_write_json(status_path, status.model_dump(mode="json"))
        logs = run_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_handle = (logs / "runtime-process.log").open("ab")
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "generative_agents.cli.main",
                    "run",
                    "resume",
                    str(run_root),
                ],
                cwd=str(Path.cwd()),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                env={**os.environ, **(environment or {}), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        except Exception:
            log_handle.close()
            self._slots.release()
            status.status = RunState.CREATED
            status.reason = "Runtime process could not be started"
            status.updated_at = datetime.now(UTC)
            atomic_write_json(status_path, status.model_dump(mode="json"))
            raise
        finally:
            # Popen duplicates/inherits the OS handle; the Studio process must
            # not keep its own handle open across checkpoint or archive work.
            if not log_handle.closed:
                log_handle.close()
        with self._lock:
            self._processes[manifest.run_id] = process
        thread = threading.Thread(
            target=self._wait,
            args=(manifest.run_id, run_root, process),
            name=f"ga-runtime-{manifest.run_id}",
            daemon=True,
        )
        thread.start()
        return manifest.run_id

    def process_status(self, run_id: str) -> dict:
        with self._lock:
            process = self._processes.get(run_id)
        return {
            "run_id": run_id,
            "process_id": process.pid if process else None,
            "return_code": process.poll() if process else None,
            "owned_by_this_studio": process is not None,
        }

    def _wait(self, run_id: str, run_root: Path, process: subprocess.Popen) -> None:
        try:
            return_code = process.wait()
            if return_code and (run_root / "status.json").is_file():
                status = RunStatus.model_validate(read_json(run_root / "status.json"))
                if status.status in {RunState.QUEUED, RunState.RUNNING, RunState.FINALIZING}:
                    status.status = RunState.FAILED
                    status.reason = f"Runtime process exited with code {return_code}"
                    status.updated_at = datetime.now(UTC)
                    atomic_write_json(run_root / "status.json", status.model_dump(mode="json"))
            if self._on_complete is not None:
                self._on_complete(run_root)
        finally:
            with self._lock:
                self._processes.pop(run_id, None)
            self._slots.release()
