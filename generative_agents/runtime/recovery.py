"""Rewind disposable query projections to a verified recovery checkpoint."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock
from sqlalchemy import delete, update

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunArtifact,
    RunConversation,
    RunConversationParticipant,
    RunDomainEvent,
    RunDomainEventAgent,
    RunEvent,
    RunMemoryEvent,
    RunMessage,
    RunRelationshipEdge,
    RunResultSummary,
    RunScheduleRevision,
    RunStep,
)

from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .context import RunPaths
from .frame_store import FrameStore, StoredFrame
from .results import StepResult
from .sqlite_result_projector import SqliteResultProjector


class RunProjectionRewinder:
    """Rebuild all mutable result views from immutable frames up to one step."""

    _PROJECTION_MODELS = (
        RunDomainEventAgent,
        RunConversationParticipant,
        RunMessage,
        RunDomainEvent,
        RunConversation,
        RunMemoryEvent,
        RunScheduleRevision,
        RunRelationshipEdge,
        RunAgentStep,
        RunAgentSummary,
        RunStep,
        RunResultSummary,
    )

    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._var_dir = Path(var_dir).resolve()

    def rewind(self, run_id: str, boundary: int) -> int:
        if boundary < 0:
            raise ValueError("recovery boundary must not be negative")
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        paths.ensure()
        with FileLock(str(paths.worker_lock), timeout=5), FileLock(
            str(paths.artifact_lock), timeout=5
        ):
            with self._database.session_factory() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError("run does not exist")
                if run.slot_no is not None or run.current_attempt_id is not None:
                    raise RuntimeError("cannot rewind an active Run")
                if boundary != run.recoverable_step or boundary > run.completed_steps:
                    raise RuntimeError("rewind boundary is not the durable Run boundary")
                summary = session.get(RunResultSummary, run_id)
                old_version = summary.result_version if summary else 0

            orphan_batch = paths.orphaned / f"rewind-{uuid4()}"
            self._quarantine_newer_frames(paths, boundary, orphan_batch / "frames")
            self._select_checkpoint(paths, boundary, orphan_batch / "checkpoints")

            with self._database.session_factory.begin() as session:
                for model in self._PROJECTION_MODELS:
                    session.execute(delete(model).where(model.run_id == run_id))
                run = session.get(Run, run_id)
                run.completed_steps = 0
                run.recoverable_step = 0
                run.virtual_time = None
                session.execute(
                    update(RunArtifact)
                    .where(RunArtifact.run_id == run_id, RunArtifact.state == "READY")
                    .values(state="STALE")
                )

            store = FrameStore(paths)
            projector = SqliteResultProjector(self._database, var_dir=self._var_dir)
            for step_no in range(1, boundary + 1):
                document = store.read_document(step_no)
                result = StepResult.from_dict(document["result"])
                path = store.path_for(step_no)
                frame = StoredFrame(
                    path=path,
                    sha256=self._sha256(path),
                    created=False,
                )
                checkpoint_path = (
                    paths.checkpoints / f"step-{boundary:06d}"
                    if step_no == boundary
                    else None
                )
                projector.commit_step(
                    result,
                    frame=frame,
                    checkpoint_path=checkpoint_path,
                    allow_reconcile=True,
                )

            now = datetime.now(timezone.utc)
            with self._database.session_factory.begin() as session:
                summary = session.get(RunResultSummary, run_id)
                result_version = old_version + 1
                if summary is not None:
                    summary.result_version = max(summary.result_version, result_version)
                    summary.updated_at = now
                    result_version = summary.result_version
                run = session.get(Run, run_id)
                run.completed_steps = boundary
                run.recoverable_step = boundary
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="result_rewound",
                        payload_json={
                            "available_step": boundary,
                            "recoverable_step": boundary,
                            "result_version": result_version,
                        },
                        created_at=now,
                    )
                )
            return result_version

    @staticmethod
    def _quarantine_newer_frames(paths, boundary: int, destination: Path) -> None:
        frame_root = paths.frames.resolve()
        for frame in sorted(paths.frames.glob("step-*.json.gz")):
            try:
                step_no = int(frame.name[5:11])
            except ValueError:
                continue
            if step_no <= boundary:
                continue
            resolved = frame.resolve()
            if resolved.parent != frame_root or frame.is_symlink():
                raise RuntimeError("unsafe future frame path")
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / frame.name
            if target.exists():
                raise RuntimeError("future frame was already quarantined")
            os.replace(resolved, target)

    @staticmethod
    def _select_checkpoint(paths, boundary: int, destination: Path) -> None:
        reader = CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )
        if boundary > 0:
            reader.select_for_recovery(boundary, orphan_root=destination)
            return
        with reader.access():
            checkpoint_root = paths.checkpoints.resolve()
            for checkpoint in sorted(paths.checkpoints.glob("step-*")):
                resolved = checkpoint.resolve()
                if resolved.parent != checkpoint_root or checkpoint.is_symlink():
                    raise RuntimeError("unsafe checkpoint path")
                destination.mkdir(parents=True, exist_ok=True)
                os.replace(resolved, destination / checkpoint.name)
            (paths.checkpoints / "LATEST").unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
