"""Small durable file projector used by the transitional CLI runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from .context import RunPaths
from .frame_store import StoredFrame
from .results import StepResult


class FileResultProjector:
    """Atomically persist the visible boundary when SQLite projection is not attached."""

    def __init__(self, paths: RunPaths):
        self._paths = paths
        self._path = paths.root / "projection.json"
        paths.ensure()

    def commit_step(
        self,
        result: StepResult,
        *,
        frame: StoredFrame,
        checkpoint_path: Path | None,
    ) -> int:
        current = self.read()
        available_step = current.get("available_step", 0)
        if result.step_no <= available_step:
            existing = current.get("steps", {}).get(str(result.step_no))
            if existing and existing["frame_sha256"] == frame.sha256:
                return current["result_version"]
            raise ValueError("result projection cannot rewrite a committed step")
        if result.step_no != available_step + 1:
            raise ValueError("result projection steps must be contiguous")
        steps = dict(current.get("steps", {}))
        steps[str(result.step_no)] = {
            "frame_sha256": frame.sha256,
            "frame": frame.path.relative_to(self._paths.root).as_posix(),
            "checkpoint": (
                checkpoint_path.relative_to(self._paths.root).as_posix()
                if checkpoint_path
                else None
            ),
            "agents": len(result.agents),
            "conversations": len(result.conversations),
            "messages": sum(len(item.messages) for item in result.conversations),
            "memory_deltas": len(result.memory_deltas),
        }
        document = {
            "run_id": str(self._paths.run_id),
            "available_step": result.step_no,
            "virtual_time": result.virtual_time.isoformat(),
            "result_version": current.get("result_version", 0) + 1,
            "steps": steps,
        }
        temporary = self._path.with_name(f".projection-{uuid4()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as file_handle:
                json.dump(
                    document,
                    file_handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
        return document["result_version"]

    def read(self) -> dict:
        if not self._path.exists():
            return {
                "run_id": str(self._paths.run_id),
                "available_step": 0,
                "result_version": 0,
                "steps": {},
            }
        with self._path.open("r", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        if document.get("run_id") != str(self._paths.run_id):
            raise ValueError("projection belongs to another run")
        return document
