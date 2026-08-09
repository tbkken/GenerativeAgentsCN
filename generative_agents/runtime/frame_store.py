"""Atomic and immutable storage for complete per-step result frames."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .context import RunPaths
from .results import StepResult


class FrameConflictError(RuntimeError):
    """Raised when a committed step is rewritten with different content."""


@dataclass(frozen=True, slots=True)
class StoredFrame:
    path: Path
    sha256: str
    created: bool


class FrameStore:
    SCHEMA_VERSION = 1

    def __init__(self, paths: RunPaths):
        self._paths = paths
        self._paths.ensure()

    def path_for(self, step_no: int) -> Path:
        if step_no < 1:
            raise ValueError("step_no must be greater than zero")
        return self._paths.frames / f"step-{step_no:06d}.json.gz"

    def write(self, result: StepResult) -> StoredFrame:
        if result.run_id != self._paths.run_id:
            raise ValueError("result run_id does not own this FrameStore")
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "result": result.to_dict(),
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        target = self.path_for(result.step_no)

        if target.exists():
            existing = target.read_bytes()
            if existing != compressed:
                raise FrameConflictError(
                    f"step {result.step_no} already has different immutable content"
                )
            return StoredFrame(path=target, sha256=digest, created=False)

        temporary = self._paths.temporary / f"frame-{result.step_no}-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as file_handle:
                file_handle.write(compressed)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredFrame(path=target, sha256=digest, created=True)

    def read_document(self, step_no: int) -> dict:
        target = self.path_for(step_no)
        with gzip.open(target, "rt", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        if document.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported frame schema at {target}")
        result = document.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"missing result object at {target}")
        if result.get("run_id") != str(self._paths.run_id):
            raise ValueError(f"frame run_id mismatch at {target}")
        if result.get("step_no") != step_no:
            raise ValueError(f"frame step_no mismatch at {target}")
        return document

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
