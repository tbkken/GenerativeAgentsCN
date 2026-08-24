"""Build immutable derived artifacts from committed run facts."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock
from sqlalchemy import func, or_, select

from generative_agents.config import ExperimentDefinition, canonical_json_bytes
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ArtifactJob,
    ExperimentRevision,
    Run,
    RunConversation,
    RunEvent,
    RunMemoryEvent,
    RunMessage,
    RunResultSummary,
    RunStep,
    RunArtifact,
)
from generative_agents.services.replay_frames import VerifiedRunFrameReader
from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .context import RunPaths
from .replay_v2 import GENERATOR_VERSION, build_replay_v2
from .artifact_contract import REPORT_GENERATOR_VERSION




class ArtifactBuilder:
    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self._frames = VerifiedRunFrameReader(self._var_dir)

    def build(self, job_id: str) -> str:
        with self._database.session_factory() as session:
            job = session.get(ArtifactJob, job_id)
            if job is None or job.status != "RUNNING":
                raise RuntimeError("artifact job is not owned in RUNNING state")
            run_id = job.run_id
            job_type = job.job_type
            parameters = dict(job.parameters_json)
            source_step = job.source_step
            partial = job.partial
            generator_version = job.generator_version
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        paths.ensure()
        # Keep Windows paths comfortably below MAX_PATH even when pytest or an
        # operator chooses a long var_dir.  The DB job UUID remains the public
        # identity; this deterministic digest is only the opaque storage key.
        storage_key = hashlib.sha256(job_id.encode("ascii")).hexdigest()[:16]
        export_dir = paths.artifacts / "exports" / storage_key
        export_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(paths.artifact_lock), timeout=30):
            target, artifact_type, media_type = self._build_file(
                job_id,
                run_id,
                job_type,
                parameters,
                source_step,
                partial,
                generator_version,
                export_dir,
                paths,
            )
            digest = self._sha256(target)
            now = datetime.now(timezone.utc)
            relative = target.relative_to(self._var_dir).as_posix()
            with self._database.session_factory.begin() as session:
                job = session.get(ArtifactJob, job_id)
                if job is None or job.status != "RUNNING":
                    raise RuntimeError("artifact job ownership changed during build")
                artifact = session.scalar(
                    select(RunArtifact).where(
                        RunArtifact.run_id == run_id,
                        RunArtifact.artifact_type == artifact_type,
                        RunArtifact.logical_name == target.name,
                        RunArtifact.generator_version == generator_version,
                        RunArtifact.source_step == source_step,
                    )
                )
                if artifact is None:
                    artifact = RunArtifact(
                        run_id=run_id,
                        artifact_type=artifact_type,
                        logical_name=target.name,
                        media_type=media_type,
                        relative_path=relative,
                        size_bytes=target.stat().st_size,
                        sha256=digest,
                        source_kind="DERIVED",
                        generator_version=generator_version,
                        source_step=source_step,
                        partial=partial,
                        state="READY",
                        created_at=now,
                    )
                    session.add(artifact)
                    session.flush()
                else:
                    if (
                        artifact.sha256 != digest
                        or artifact.size_bytes != target.stat().st_size
                        or artifact.relative_path != relative
                    ):
                        raise RuntimeError("immutable artifact identity already has different content")
                job.status = "SUCCEEDED"
                job.progress = 1
                job.artifact_id = artifact.id
                job.finished_at = now
                job.worker_pid = None
                job.pid_create_time = None
                job.heartbeat_at = now
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="artifact_ready",
                        payload_json={
                            "job_id": job.id,
                            "artifact_id": artifact.id,
                            "artifact_type": artifact.artifact_type,
                            "source_step": source_step,
                            "generator_version": generator_version,
                            "partial": partial,
                        },
                        created_at=now,
                    )
                )
                return artifact.id

    def fail(self, job_id: str, exc: Exception) -> None:
        now = datetime.now(timezone.utc)
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, job_id)
            if job is None or job.status != "RUNNING":
                return
            job.status = "FAILED"
            job.error_summary = f"{type(exc).__name__}: {exc}"[:2000]
            job.finished_at = now
            job.worker_pid = None
            job.pid_create_time = None
            job.heartbeat_at = now
            session.add(
                RunEvent(
                    run_id=job.run_id,
                    event_type="artifact_error",
                    payload_json={"job_id": job.id, "error": job.error_summary},
                    created_at=now,
                )
            )

    def _build_file(
        self,
        job_id,
        run_id,
        job_type,
        parameters,
        source_step,
        partial,
        generator_version,
        export_dir,
        paths,
    ):
        if job_type == "BUILD_REPLAY":
            if generator_version != GENERATOR_VERSION:
                raise RuntimeError("unsupported replay generator version")
            target = export_dir / f"replay-step-{source_step:06d}.v2.json"
            self._atomic_json(
                target,
                self._replay_document(
                    run_id, source_step=source_step, partial=partial
                ),
            )
            return target, "REPLAY", "application/json"
        if job_type == "BUILD_REPORT":
            if generator_version != REPORT_GENERATOR_VERSION:
                raise RuntimeError("unsupported report generator version")
            target = export_dir / f"report-step-{source_step:06d}.md"
            self._atomic_text(
                target,
                self._report_document(run_id, source_step=source_step, partial=partial),
            )
            return target, "REPORT", "text/markdown"
        if job_type == "FILTERED_MEMORIES":
            target = export_dir / f"memories-step-{source_step:06d}.json"
            self._atomic_json(
                target,
                self._memory_document(
                    run_id, parameters, source_step=source_step, partial=partial
                ),
            )
            return target, "MEMORY_EXPORT", "application/json"
        if job_type == "FILTERED_CONVERSATIONS":
            target = export_dir / f"conversations-step-{source_step:06d}.json"
            self._atomic_json(
                target,
                self._conversation_document(
                    run_id, parameters, source_step=source_step, partial=partial
                ),
            )
            return target, "CONVERSATION_EXPORT", "application/json"
        if job_type == "CHECKPOINT_BUNDLE":
            target = export_dir / f"checkpoint-step-{source_step:06d}.zip"
            self._checkpoint_bundle(target, paths, parameters)
            return target, "CHECKPOINT_BUNDLE", "application/zip"
        if job_type == "RESULT_BUNDLE":
            target = export_dir / f"results-step-{source_step:06d}.zip"
            self._result_bundle(
                target,
                run_id,
                paths,
                source_step=source_step,
                partial=partial,
            )
            return target, "RESULT_BUNDLE", "application/zip"
        raise RuntimeError(f"unsupported artifact job type: {job_type}")

    def _replay_document(
        self,
        run_id: str,
        *,
        source_step: int | None = None,
        partial: bool | None = None,
    ) -> dict:
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeError("artifact run does not exist")
            revision = session.get(ExperimentRevision, run.revision_id)
            if revision is None:
                raise RuntimeError("artifact revision does not exist")
            available = self._available_step(session, run_id)
            locked_step = available if source_step is None else source_step
            if locked_step < 0 or locked_step > available:
                raise RuntimeError("artifact source_step exceeds committed results")
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            checkpoint_steps = set(
                session.scalars(
                    select(RunStep.step_no).where(
                        RunStep.run_id == run_id,
                        RunStep.step_no <= locked_step,
                        RunStep.checkpoint.is_(True),
                    )
                )
            )
            frame_rows = list(
                session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.run_id == run_id,
                        RunStep.step_no <= locked_step,
                    )
                    .order_by(RunStep.step_no)
                )
            )
            if [row.step_no for row in frame_rows] != list(range(1, locked_step + 1)):
                raise RuntimeError("artifact source contains a missing committed frame")
            revision_id = revision.id
            definition_hash = revision.definition_hash
            is_partial = (
                run.status != "COMPLETED" or locked_step < run.requested_steps
                if partial is None
                else partial
            )
            session.expunge(run)
        results = [self._frames.read(run, row) for row in frame_rows]
        return build_replay_v2(
            run_id=run_id,
            revision_id=revision_id,
            definition_hash=definition_hash,
            definition=definition,
            source_step=locked_step,
            partial=is_partial,
            results=results,
            checkpoint_steps=checkpoint_steps,
        )

    def _report_document(self, run_id: str, *, source_step: int, partial: bool) -> str:
        summary = self._source_summary(run_id, source_step=source_step, partial=partial)
        replay = self._replay_document(
            run_id,
            source_step=source_step,
            partial=partial,
        )
        lines = [
            "# Run report",
            "",
            f"- Run: `{run_id}`",
            f"- Source step: {source_step}",
            f"- Scope: {'partial' if partial else 'final'}",
            f"- Conversations: {summary['counts']['conversations']}",
            f"- Memories: {summary['counts']['memories']}",
            f"- Model calls: {summary['counts']['model_calls']}",
            "",
            "## Timeline",
            "",
        ]
        cognitive_kinds = {
            "EVENT_PERCEIVED",
            "MEMORY_CREATED",
            "MEMORY_ACCESSED",
            "MEMORY_EXPIRED",
            "MEMORY_EVICTED",
            "REFLECTION_CREATED",
            "SCHEDULE_REVISED",
            "SKILL_EXECUTED",
        }
        for step in replay["steps"]:
            lines.extend(
                [
                    f"### Step {step['step_no']} · {step['virtual_time']}",
                    "",
                ]
            )
            for agent in step["agents"]:
                action = agent.get("action") or {}
                lines.append(
                    f"- **{agent['agent_key']}**: "
                    f"{action.get('description') or 'idle'} @ "
                    f"{' / '.join(agent.get('address') or ())}"
                )
            for conversation in step["conversations"]:
                messages = " | ".join(
                    f"{message['speaker_agent_key']}: {message['content']}"
                    for message in conversation.get("messages", ())
                )
                lines.append(f"- **CONVERSATION**: {messages}")
            for effect in step.get("effects", ()):
                if effect.get("kind") not in cognitive_kinds:
                    continue
                payload = effect.get("payload") or {}
                detail = (
                    payload.get("description")
                    or payload.get("output_text")
                    or payload.get("event_type")
                    or payload.get("memory_id")
                    or ""
                )
                agents = ", ".join(effect.get("agent_keys") or ())
                lines.append(
                    f"- **{effect['kind']}**"
                    f"{f' ({agents})' if agents else ''}: {detail}"
                )
            lines.append("")
        return "\n".join(lines)

    def _memory_document(
        self,
        run_id: str,
        parameters: dict,
        *,
        source_step: int,
        partial: bool,
    ) -> dict:
        with self._database.session_factory() as session:
            statement = select(RunMemoryEvent).where(
                RunMemoryEvent.run_id == run_id,
                or_(
                    RunMemoryEvent.created_step.is_(None),
                    RunMemoryEvent.created_step <= source_step,
                ),
            )
            for name, column in (
                ("agent_key", RunMemoryEvent.agent_key),
                ("memory_type", RunMemoryEvent.memory_type),
            ):
                if parameters.get(name):
                    statement = statement.where(column == parameters[name])
            if parameters.get("q"):
                statement = statement.where(
                    RunMemoryEvent.description.ilike(f"%{parameters['q']}%")
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        RunMemoryEvent.created_step,
                        RunMemoryEvent.agent_key,
                        RunMemoryEvent.memory_node_id,
                    )
                )
            )
        state_filter = parameters.get("state")
        items = []
        for row in rows:
            removed_at_source = (
                row.removed_step is not None and row.removed_step <= source_step
            )
            effective_state = "REMOVED" if removed_at_source else "ACTIVE"
            if state_filter and state_filter != effective_state:
                continue
            items.append(
                {
                    "memory_id": row.memory_node_id,
                    "agent_key": row.agent_key,
                    "type": row.memory_type,
                    "origin": row.origin,
                    "state": effective_state,
                    "description": row.description,
                    "poignancy": row.poignancy,
                    "created_step": row.created_step,
                    "last_accessed_step": (
                        row.last_accessed_step
                        if row.last_accessed_step is not None
                        and row.last_accessed_step <= source_step
                        else None
                    ),
                    "removed_step": row.removed_step if removed_at_source else None,
                }
            )
        return {
            "schema_version": 1,
            "run_id": run_id,
            "source_step": source_step,
            "partial": partial,
            "filters": parameters,
            "items": items,
        }

    def _conversation_document(
        self,
        run_id: str,
        parameters: dict,
        *,
        source_step: int,
        partial: bool,
    ) -> dict:
        with self._database.session_factory() as session:
            conversations = list(
                session.scalars(
                    select(RunConversation)
                    .where(
                        RunConversation.run_id == run_id,
                        RunConversation.start_step <= source_step,
                    )
                    .order_by(RunConversation.start_step, RunConversation.id)
                )
            )
            messages = list(
                session.scalars(
                    select(RunMessage)
                    .where(
                        RunMessage.run_id == run_id,
                        RunMessage.source_step <= source_step,
                    )
                    .order_by(RunMessage.conversation_id, RunMessage.sequence_no)
                )
            )
        by_conversation: dict[str, list[dict]] = defaultdict(list)
        for row in messages:
            by_conversation[row.conversation_id].append(
                {
                    "sequence": row.sequence_no,
                    "speaker_agent_key": row.speaker_agent_key,
                    "content": row.content,
                    "observed_at": row.observed_at.isoformat(),
                }
            )
        agent_key = parameters.get("agent_key")
        query = str(parameters.get("q") or "").casefold()
        items = []
        for row in conversations:
            participants = [row.initiator_agent_key, row.responder_agent_key]
            if agent_key and agent_key not in participants:
                continue
            conversation_messages = by_conversation[row.id]
            haystack = " ".join(
                [row.summary or "", *participants, *(item["content"] for item in conversation_messages)]
            ).casefold()
            if query and query not in haystack:
                continue
            items.append(
                {
                    "conversation_id": row.id,
                    "start_step": row.start_step,
                    "started_at": row.started_at.isoformat(),
                    "participants": participants,
                    "location": row.location,
                    "end_step": min(row.end_step, source_step),
                    "ended_at": row.ended_at.isoformat() if row.end_step <= source_step else None,
                    "duration_minutes": row.duration_minutes if row.end_step <= source_step else None,
                    "summary": row.summary if row.end_step <= source_step else None,
                    "partial_at_source": row.end_step > source_step,
                    "messages": conversation_messages,
                }
            )
        return {
            "schema_version": 1,
            "run_id": run_id,
            "source_step": source_step,
            "partial": partial,
            "filters": parameters,
            "items": items,
        }

    def _checkpoint_bundle(
        self, target: Path, paths: RunPaths, parameters: dict
    ) -> None:
        reader = CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )
        step_no = parameters.get("checkpoint_step")
        if not isinstance(step_no, int) or isinstance(step_no, bool) or step_no < 1:
            raise RuntimeError("checkpoint_step must select a positive checkpoint step")
        with reader.access():
            checkpoint = reader.validate(paths.checkpoints / f"step-{step_no:06d}")
            self._atomic_zip_tree(target, checkpoint.path, prefix=checkpoint.path.name)

    def _result_bundle(
        self,
        target: Path,
        run_id: str,
        paths: RunPaths,
        *,
        source_step: int,
        partial: bool,
    ) -> None:
        summary = self._source_summary(run_id, source_step=source_step, partial=partial)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}-{uuid4()}.tmp"
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.writestr("summary.json", canonical_json_bytes(summary))
                if paths.manifest.is_file():
                    archive.write(paths.manifest, "manifest.json")
                for frame in sorted(paths.frames.glob("step-*.json.gz")):
                    try:
                        step_no = int(frame.name[5:11])
                    except ValueError:
                        continue
                    if step_no <= source_step and not frame.is_symlink():
                        archive.write(frame, f"frames/{frame.name}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_json(target: Path, document: dict) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical_json_bytes(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_text(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_zip_tree(target: Path, root: Path, *, prefix: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}-{uuid4()}.tmp"
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _available_step(session, run_id: str) -> int:
        if session.get(Run, run_id) is None:
            raise RuntimeError("artifact run does not exist")
        summary = session.get(RunResultSummary, run_id)
        return summary.available_step if summary else 0

    def _source_summary(
        self, run_id: str, *, source_step: int, partial: bool
    ) -> dict:
        """Build a summary solely from facts committed at the frozen boundary."""

        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeError("artifact run does not exist")
            if source_step < 0 or source_step > self._available_step(session, run_id):
                raise RuntimeError("artifact source_step exceeds committed results")
            totals = session.execute(
                select(
                    func.coalesce(func.sum(RunStep.action_count), 0),
                    func.coalesce(func.sum(RunStep.conversation_count), 0),
                    func.coalesce(func.sum(RunStep.message_count), 0),
                    func.coalesce(func.sum(RunStep.memory_created_count), 0),
                    func.coalesce(func.sum(RunStep.model_logical_calls), 0),
                    func.coalesce(func.sum(RunStep.model_retry_count), 0),
                    func.max(RunStep.virtual_time),
                ).where(RunStep.run_id == run_id, RunStep.step_no <= source_step)
            ).one()
        return {
            "run_id": run_id,
            "run_status": run.status,
            "result_state": "PARTIAL" if partial else "COMPLETE",
            "available_step": source_step,
            "source_step": source_step,
            "requested_steps": run.requested_steps,
            "partial": partial,
            "virtual_time": totals[6].isoformat() if totals[6] else None,
            "counts": {
                "actions": int(totals[0]),
                "conversations": int(totals[1]),
                "messages": int(totals[2]),
                "memories": int(totals[3]),
                "model_calls": int(totals[4]),
                "model_retries": int(totals[5]),
            },
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
