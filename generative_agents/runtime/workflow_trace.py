"""Append-only execution evidence for workflow nodes in one Run attempt."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from .context import RunPaths


class WorkflowTraceWriter:
    """Write metadata-only workflow traces without persisting prompt payloads."""

    def __init__(self, paths: RunPaths, *, attempt_id: UUID) -> None:
        paths.ensure()
        self.path: Path = paths.traces / f"workflow-nodes-{attempt_id}.jsonl"
        self._attempt_id = str(attempt_id)
        self._lock = threading.Lock()
        self._event_seq = 0

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self._event_seq += 1
            document = {
                "event_seq": self._event_seq,
                "attempt_id": self._attempt_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **dict(event),
            }
            line = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


__all__ = ["WorkflowTraceWriter"]
