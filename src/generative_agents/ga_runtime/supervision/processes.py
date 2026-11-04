"""Local file-only process supervision for portable Run directories."""

from __future__ import annotations

import subprocess
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.validation import validate_run_directory
from generative_agents.ga_runtime.supervision.ownership import LAUNCH_ENV, process_observation, reconcile_run, submit_launch


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
        self._max_concurrent_runs = max_concurrent_runs
        self._on_complete = on_complete
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def submit(self, run_directory: str | Path, *, environment: dict[str, str] | None = None) -> str:
        run_root = checked_package_path(Path(run_directory))
        manifest = validate_run_directory(run_root)
        logs = run_root / "logs"
        checked_package_path(logs / "runtime-process.log")
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with submit_launch(run_root, self._max_concurrent_runs) as token:
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "runtime-process.log").open("ab") as log_handle:
                process = subprocess.Popen(
                    [sys.executable, "-m", "generative_agents.adapters.cli.main", "run", "resume", str(run_root)],
                    cwd=str(Path.cwd()), stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                    env={**os.environ, **(environment or {}), "PYTHONUTF8": "1",
                         "PYTHONIOENCODING": "utf-8", LAUNCH_ENV: token},
                )
        with self._lock:
            self._processes[manifest.run_id] = process
        thread = threading.Thread(
            target=self._wait,
            args=(manifest.run_id, run_root, process, token),
            name=f"ga-runtime-{manifest.run_id}",
            daemon=True,
        )
        thread.start()
        return manifest.run_id

    def process_status(self, run_id: str, run_directory: str | Path | None = None) -> dict:
        with self._lock:
            process = self._processes.get(run_id)
        observation = process_observation(Path(run_directory)) if run_directory is not None else {}
        return {
            **observation,
            "run_id": run_id,
            "process_id": observation.get("process_id") or (process.pid if process else None),
            "return_code": process.poll() if process else None,
            "owned_by_this_studio": process is not None,
        }

    def _wait(self, run_id: str, run_root: Path, process: subprocess.Popen, token: str) -> None:
        try:
            return_code = process.wait()
            if (run_root / "status.json").is_file():
                reconcile_run(run_root, expected_token=token, reason=f"仿真执行进程退出，退出码 {return_code}。")
            if self._on_complete is not None:
                self._on_complete(run_root)
        finally:
            with self._lock:
                if self._processes.get(run_id) is process:
                    self._processes.pop(run_id)
