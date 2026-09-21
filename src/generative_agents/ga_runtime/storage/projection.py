"""Disposable file projection of immutable StepResult frames."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_runtime.engine.context import RunPaths
from generative_agents.ga_runtime.storage.frames import FrameStore
from generative_agents.ga_runtime.storage.frames import StoredFrame
from generative_agents.ga_protocol.schemas.facts import StepResult


class FileResultProjector:
    """Single-writer cache; status and immutable frames own the commit boundary."""

    def __init__(self, paths: RunPaths):
        self._paths = paths
        self._path = paths.root / "projection.json"
        self._current = None
        self._write_deferred = False
        paths.ensure()

    def _append(self, current, result, frame, checkpoint_path):
        available = current.get("available_step", 0)
        if result.step_no <= available:
            existing = current.get("steps", {}).get(str(result.step_no))
            if existing and existing["frame_sha256"] == frame.sha256:
                return current
            raise ValueError("result projection cannot rewrite a committed step")
        if result.step_no != available + 1:
            raise ValueError("result projection steps must be contiguous")
        steps = dict(current.get("steps", {}))
        steps[str(result.step_no)] = {
            "frame_sha256": frame.sha256,
            "frame": frame.path.relative_to(self._paths.root).as_posix(),
            "checkpoint": checkpoint_path.relative_to(self._paths.root).as_posix() if checkpoint_path else None,
            "agents": len(result.agents),
            "conversations": len(result.conversations),
            "messages": sum(len(item.messages) for item in result.conversations),
            "memory_deltas": len(result.memory_deltas),
        }
        return {
            "run_id": str(self._paths.run_id), "available_step": result.step_no,
            "virtual_time": result.virtual_time.isoformat(),
            "result_version": current.get("result_version", 0) + 1, "steps": steps,
        }

    def commit_step(self, result: StepResult, *, frame: StoredFrame,
                    checkpoint_path: Path | None) -> int:
        document = self._append(self.read(), result, frame, checkpoint_path)
        try:
            atomic_write_json(self._path, document)
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            # Only this derived cache is optional. Frame/checkpoint/status IO
            # failures still propagate and cannot advance the durable boundary.
            if not self._write_deferred:
                logging.getLogger(__name__).warning(
                    "PROJECTION_WRITE_DEFERRED run=%s step=%s path=%s: %s; "
                    "Replay will read committed frames; retry on the next Step",
                    self._paths.run_id, result.step_no, self._path, exc,
                )
            self._write_deferred = True
        else:
            if self._write_deferred:
                logging.getLogger(__name__).warning(
                    "PROJECTION_WRITE_RECOVERED run=%s step=%s", self._paths.run_id, result.step_no,
                )
            self._write_deferred = False
        self._current = document
        return document["result_version"]

    def read(self) -> dict:
        if self._current is not None:
            return self._current
        current = {"run_id": str(self._paths.run_id), "available_step": 0,
                   "result_version": 0, "steps": {}}
        status_path = self._paths.root / "status.json"
        if status_path.is_file():
            status = read_json(status_path)
            if status.get("run_id") != str(self._paths.run_id):
                raise ValueError("status belongs to another run")
            # Reconstruct once per worker, including after a cache write failure
            # or crash. Never include a frame beyond the durable status boundary.
            store = FrameStore(self._paths)
            for step in range(1, int(status["committed_step"]) + 1):
                result = StepResult.from_dict(store.read_document(step)["result"])
                path = store.path_for(step)
                frame = StoredFrame(path, hashlib.sha256(path.read_bytes()).hexdigest(), False)
                checkpoint = self._paths.checkpoints / f"step-{step:06d}"
                current = self._append(current, result, frame, checkpoint if (checkpoint / "bundle.json").is_file() else None)
        elif self._path.is_file():
            current = read_json(self._path)
            if current.get("run_id") != str(self._paths.run_id):
                raise ValueError("projection belongs to another run")
        self._current = current
        return current
