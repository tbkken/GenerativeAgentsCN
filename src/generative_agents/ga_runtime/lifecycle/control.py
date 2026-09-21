"""File-backed cooperative Run controls usable across local processes."""

from __future__ import annotations

import threading
from pathlib import Path

from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json


class FileRunControl:
    def __init__(self, run_root: str | Path) -> None:
        self.path = Path(run_root).resolve() / "control.json"
        self._local_pause = threading.Event()
        self._local_cancel = threading.Event()
        if not self.path.exists():
            atomic_write_json(self.path, {"pause_requested": False, "cancel_requested": False})

    def _read(self) -> dict:
        try:
            value = read_json(self.path)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, *, pause: bool | None = None, cancel: bool | None = None) -> None:
        current = self._read()
        if pause is not None:
            current["pause_requested"] = pause
        if cancel is not None:
            current["cancel_requested"] = cancel
        atomic_write_json(self.path, current)

    def reset(self) -> None:
        self._local_pause.clear()
        self._local_cancel.clear()
        self._write(pause=False, cancel=False)

    def request_pause(self) -> None:
        self._local_pause.set()
        self._write(pause=True)

    def request_cancel(self) -> None:
        self._local_cancel.set()
        self._write(cancel=True)

    @property
    def pause_requested(self) -> bool:
        return self._local_pause.is_set() or bool(self._read().get("pause_requested"))

    @property
    def cancel_requested(self) -> bool:
        return self._local_cancel.is_set() or bool(self._read().get("cancel_requested"))

