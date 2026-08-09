"""Persistent artifact jobs and run-scoped controlled downloads."""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from generative_agents.config import canonical_json_bytes
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ArtifactJob,
    Run,
    RunArtifact,
    RunEvent,
    RunResultSummary,
)
from generative_agents.runtime.artifact_contract import GENERATOR_VERSIONS

from .errors import ServiceError, not_found
from .run_storage import RunStorageBoundary


ALLOWED_JOB_TYPES = frozenset(
    {
        "BUILD_REPLAY",
        "BUILD_REPORT",
        "RESULT_BUNDLE",
        "FILTERED_MEMORIES",
        "FILTERED_CONVERSATIONS",
        "CHECKPOINT_BUNDLE",
    }
)

class ArtifactService:
    _integrity_lock = threading.RLock()
    _verified_content: OrderedDict[tuple, None] = OrderedDict()
    _MAX_VERIFIED_CONTENT = 2_048

    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self._boundary = RunStorageBoundary(self._var_dir)

    def create_job(
        self, run_id: str, *, job_type: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if job_type not in ALLOWED_JOB_TYPES:
            raise ServiceError(
                "INVALID_ARTIFACT_JOB_TYPE", "不支持的制品任务类型", status_code=422
            )
        requested_parameters = dict(parameters or {})
        if job_type == "CHECKPOINT_BUNDLE":
            # Keep one authoritative validation path even for callers that use
            # the generic job service directly instead of the dedicated API.
            checkpoint_step = requested_parameters.get("checkpoint_step")
            if not isinstance(checkpoint_step, int) or isinstance(checkpoint_step, bool):
                raise ServiceError(
                    "INVALID_ARTIFACT_SOURCE_STEP",
                    "checkpoint_step 必须选择一个检查点",
                    status_code=422,
                )
            explicit_source = requested_parameters.get("source_step")
            if explicit_source is not None and explicit_source != checkpoint_step:
                raise ServiceError(
                    "CHECKPOINT_SOURCE_STEP_MISMATCH",
                    "检查点制品的 source_step 必须与 checkpoint_step 一致",
                    status_code=422,
                )
            from .checkpoints import CheckpointService

            CheckpointService(
                self._database, var_dir=self._var_dir
            ).validate_for_export(run_id, checkpoint_step)
        now = datetime.now(timezone.utc)
        try:
            with self._database.session_factory.begin() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise not_found("run", run_id)
                summary = session.get(RunResultSummary, run_id)
                available_step = summary.available_step if summary else run.completed_steps
                requested_source = requested_parameters.pop("source_step", None)
                if requested_source is None and job_type == "CHECKPOINT_BUNDLE":
                    requested_source = requested_parameters.get("checkpoint_step")
                source_step = available_step if requested_source is None else requested_source
                invalid_source = (
                    not isinstance(source_step, int)
                    or isinstance(source_step, bool)
                    or source_step < 0
                    or (
                        job_type != "CHECKPOINT_BUNDLE"
                        and source_step > available_step
                    )
                    or (job_type == "CHECKPOINT_BUNDLE" and source_step < 1)
                )
                if invalid_source:
                    raise ServiceError(
                        "INVALID_ARTIFACT_SOURCE_STEP",
                        "制品 source_step 必须是已提交边界",
                        status_code=422,
                        details={"available_step": available_step},
                    )
                generator_version = GENERATOR_VERSIONS[job_type]
                requested_parameters.pop("generator_version", None)
                # The projection summary is the authority for result scope.
                # Run.status may be committed slightly later than its final
                # projection and must not relabel an already complete boundary.
                partial = not (
                    summary is not None
                    and summary.result_state == "COMPLETE"
                    and source_step == summary.available_step
                )
                parameters = {
                    **requested_parameters,
                    "source_step": source_step,
                    "generator_version": generator_version,
                    "partial": partial,
                }
                encoded = canonical_json_bytes(parameters)
                parameters_hash = hashlib.sha256(encoded).hexdigest()
                existing = session.scalar(
                    select(ArtifactJob)
                    .where(
                        ArtifactJob.run_id == run_id,
                        ArtifactJob.job_type == job_type,
                        ArtifactJob.parameters_hash == parameters_hash,
                        ArtifactJob.source_step == source_step,
                        ArtifactJob.generator_version == generator_version,
                        ArtifactJob.status.in_({"QUEUED", "RUNNING", "SUCCEEDED"}),
                    )
                    .order_by(ArtifactJob.created_at.desc())
                    .limit(1)
                )
                if existing is not None:
                    return self._job(existing)
                job = ArtifactJob(
                    id=str(uuid4()),
                    run_id=run_id,
                    job_type=job_type,
                    parameters_json=parameters,
                    parameters_hash=parameters_hash,
                    source_step=source_step,
                    generator_version=generator_version,
                    partial=partial,
                    status="QUEUED",
                    attempt_no=0,
                    progress=0,
                    created_at=now,
                )
                session.add(job)
                session.flush()
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="artifact_queued",
                        payload_json={
                            "job_id": job.id,
                            "job_type": job.job_type,
                            "status": job.status,
                            "progress": job.progress,
                        },
                        created_at=now,
                    )
                )
                return self._job(job)
        except IntegrityError:
            with self._database.session_factory() as session:
                existing = session.scalar(
                    select(ArtifactJob)
                    .where(
                        ArtifactJob.run_id == run_id,
                        ArtifactJob.job_type == job_type,
                        ArtifactJob.parameters_hash == parameters_hash,
                        ArtifactJob.source_step == source_step,
                        ArtifactJob.generator_version == generator_version,
                        ArtifactJob.status.in_({"QUEUED", "RUNNING"}),
                    )
                    .order_by(ArtifactJob.created_at.desc())
                    .limit(1)
                )
                if existing is None:
                    raise
                return self._job(existing)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            job = session.get(ArtifactJob, job_id)
            if job is None:
                raise not_found("artifact_job", job_id)
            return self._job(job)

    def list_artifacts(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            if session.get(Run, run_id) is None:
                raise not_found("run", run_id)
            rows = list(
                session.scalars(
                    select(RunArtifact)
                    .where(RunArtifact.run_id == run_id)
                    .order_by(RunArtifact.created_at.desc(), RunArtifact.id.desc())
                )
            )
            return {"run_id": run_id, "items": [self._artifact(row) for row in rows]}

    def get_artifact(self, run_id: str, artifact_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            artifact = session.get(RunArtifact, artifact_id)
            if artifact is None or artifact.run_id != run_id:
                raise not_found("artifact", artifact_id)
            return self._artifact(artifact)

    def content(self, run_id: str, artifact_id: str) -> tuple[RunArtifact, Path]:
        with self.open_content(run_id, artifact_id) as (artifact, path, _handle):
            return artifact, path

    @contextmanager
    def open_content(self, run_id: str, artifact_id: str) -> Iterator[tuple]:
        """Yield a verified artifact and the exact descriptor that was hashed."""

        with self._database.session_factory() as session:
            artifact = session.get(RunArtifact, artifact_id)
            run = session.get(Run, run_id)
            if artifact is None or run is None or artifact.run_id != run_id:
                raise not_found("artifact", artifact_id)
            if artifact.state != "READY":
                raise ServiceError(
                    "ARTIFACT_NOT_READY", "制品尚未生成完成", status_code=409
                )
            session.expunge(artifact)
        try:
            opened = self._boundary.open_owned_binary(
                run, artifact.relative_path, area="artifacts"
            )
            context = opened.__enter__()
        except FileNotFoundError as exc:
            raise ServiceError(
                "ARTIFACT_CONTENT_MISSING", "制品文件不存在", status_code=410
            ) from exc
        try:
            path, handle, opened_stat = context
            identity = (
                artifact.id,
                artifact.sha256,
                artifact.size_bytes,
                str(path),
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_size,
                opened_stat.st_mtime_ns,
                opened_stat.st_ctime_ns,
            )
            with self._integrity_lock:
                verified = identity in self._verified_content
                if verified:
                    self._verified_content.move_to_end(identity)
            digest_value = artifact.sha256
            if not verified:
                digest = hashlib.sha256()
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                digest_value = digest.hexdigest()
            # All newly built artifacts have an authoritative digest.  A zero
            # digest is retained as an explicit legacy/unverified sentinel for
            # pre-contract imports; size ownership is still enforced for those
            # rows and they are never emitted by ArtifactBuilder.
            digest_mismatch = artifact.sha256 != "0" * 64 and not hmac.compare_digest(
                digest_value, artifact.sha256
            )
            if opened_stat.st_size != artifact.size_bytes or digest_mismatch:
                raise ServiceError(
                    "ARTIFACT_CONTENT_INTEGRITY_ERROR",
                    "制品内容完整性校验失败",
                    status_code=500,
                )
            if not verified:
                with self._integrity_lock:
                    self._verified_content[identity] = None
                    self._verified_content.move_to_end(identity)
                    while len(self._verified_content) > self._MAX_VERIFIED_CONTENT:
                        self._verified_content.popitem(last=False)
            handle.seek(0)
            yield artifact, path, handle
        finally:
            opened.__exit__(None, None, None)

    @staticmethod
    def _job(job: ArtifactJob) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "run_id": job.run_id,
            "job_type": job.job_type,
            "parameters": job.parameters_json,
            "source_step": job.source_step,
            "generator_version": job.generator_version,
            "partial": job.partial,
            "status": job.status,
            "attempt_no": job.attempt_no,
            "log": {
                "available": bool(job.log_path),
            },
            "progress": job.progress,
            "artifact_id": job.artifact_id,
            "error_summary": job.error_summary,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    @staticmethod
    def _artifact(artifact: RunArtifact) -> dict[str, Any]:
        return {
            "artifact_id": artifact.id,
            "run_id": artifact.run_id,
            "type": artifact.artifact_type,
            "logical_name": artifact.logical_name,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "source_kind": artifact.source_kind,
            "generator_version": artifact.generator_version,
            "source_step": artifact.source_step,
            "partial": artifact.partial,
            "scope": "PARTIAL" if artifact.partial else "FINAL",
            "state": artifact.state,
            "created_at": artifact.created_at.isoformat(),
            "error_summary": artifact.error_summary,
        }
