"""Commit ordering for frames, checkpoints, and query projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .frame_store import FrameStore, StoredFrame
from .results import StepResult


class CheckpointWriter(Protocol):
    def write(self, result: StepResult, frame: StoredFrame) -> Path: ...


class StepProjection(Protocol):
    def commit_step(
        self,
        result: StepResult,
        *,
        frame: StoredFrame,
        checkpoint_path: Path | None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    step_no: int
    frame_path: Path
    frame_sha256: str
    checkpoint_path: Path | None
    result_version: int


class FileStepCommitter:
    """The only worker API allowed to advance the projected available step."""

    def __init__(
        self,
        frame_store: FrameStore,
        projection: StepProjection,
        checkpoint_writer: CheckpointWriter | None = None,
    ):
        self._frame_store = frame_store
        self._projection = projection
        self._checkpoint_writer = checkpoint_writer

    def commit(self, result: StepResult, *, force_checkpoint: bool) -> CommitReceipt:
        frame = self._frame_store.write(result)
        checkpoint_path = None
        if force_checkpoint:
            if self._checkpoint_writer is None:
                raise RuntimeError("force_checkpoint requires a checkpoint writer")
            checkpoint_path = self._checkpoint_writer.write(result, frame)
        result_version = self._projection.commit_step(
            result,
            frame=frame,
            checkpoint_path=checkpoint_path,
        )
        return CommitReceipt(
            step_no=result.step_no,
            frame_path=frame.path,
            frame_sha256=frame.sha256,
            checkpoint_path=checkpoint_path,
            result_version=result_version,
        )
