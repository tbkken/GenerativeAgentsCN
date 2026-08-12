"""Transactional run creation, history pagination, and control state changes."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID, uuid4

from filelock import FileLock
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from generative_agents.config import ExperimentDefinition, validate_for_publish
from generative_agents.config.scenarios import ExperimentCapabilityExtension
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Experiment,
    ExperimentRevision,
    ExperimentRevisionCapability,
    Run,
    RunAttempt,
    RunEvent,
    RunQueue,
    RunResultSummary,
)
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot

from .errors import ServiceError, not_found

if TYPE_CHECKING:
    from .model_probes import ModelProbeService


OPEN_RUN_STATUSES = frozenset(
    {
        "QUEUED",
        "STARTING",
        "RUNNING",
        "PAUSE_REQUESTED",
        "PAUSED",
        "CANCEL_REQUESTED",
    }
)
OCCUPYING_RUN_STATUSES = frozenset(
    {"STARTING", "RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """Serialize persisted instants without losing SQLite's implicit UTC zone."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _encode_cursor(created_at: datetime, run_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": run_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        created_at = datetime.fromisoformat(payload["created_at"])
        UUID(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            "INVALID_CURSOR", "运行历史游标无效", status_code=422
        ) from exc
    return created_at, payload["id"]


def _run_shape(
    session: Session,
    revision: ExperimentRevision,
    definition: ExperimentDefinition,
) -> tuple[int, int]:
    """Return durable run steps and legacy stride for either execution mode."""

    row = session.get(ExperimentRevisionCapability, revision.id)
    if row is None:
        return definition.simulation.max_steps, definition.simulation.stride_minutes
    extension = ExperimentCapabilityExtension.model_validate(row.extension_json)
    if extension.mode == "LEGACY_TOWN":
        return definition.simulation.max_steps, definition.simulation.stride_minutes
    requested_steps = (
        extension.clock.duration_ms + extension.clock.snapshot_interval_ms - 1
    ) // extension.clock.snapshot_interval_ms
    return max(1, requested_steps), 1


class RunService:
    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        now: Callable[[], datetime] = _utc_now,
        model_probes: ModelProbeService | None = None,
    ):
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self._now = now
        self._model_probes = model_probes

    def publish_and_run(
        self,
        experiment_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Publish the draft and enqueue its first Run in one transaction."""

        from .experiments import ExperimentService

        requires_models = True
        with self._database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            draft = session.get(
                ExperimentRevision,
                experiment.current_draft_revision_id if experiment else None,
            )
            if draft is not None:
                from .scenarios import composed_scenario_requires_models_in_session

                requires_models = composed_scenario_requires_models_in_session(
                    session, draft
                )
        if self._model_probes is not None and requires_models:
            prepared = self._model_probes.resolve_for_publish(
                experiment_id,
                expected_lock_version=expected_lock_version,
            )
            draft_revision_id = prepared["draft_revision_id"]
            expected_lock_version = prepared["lock_version"]

        now = self._now()
        run_id = str(uuid4())
        try:
            with self._database.session_factory.begin() as session:
                existing = session.scalar(
                    select(Run.id).where(
                        Run.experiment_id == experiment_id,
                        Run.status.in_(OPEN_RUN_STATUSES),
                    )
                )
                if existing is not None:
                    raise ServiceError(
                        "EXPERIMENT_RUN_ACTIVE",
                        "该实验已有未结束运行",
                        status_code=409,
                        details={"run_id": existing},
                    )
                revision = ExperimentService(self._database).publish_draft_in_session(
                    session,
                    experiment_id=experiment_id,
                    draft_revision_id=draft_revision_id,
                    expected_lock_version=expected_lock_version,
                )
                experiment = session.get(Experiment, experiment_id)
                definition = ExperimentDefinition.model_validate(revision.definition_json)
                requested_steps, stride_minutes = _run_shape(
                    session, revision, definition
                )
                paths = RunPaths.under(self._var_dir, UUID(run_id))
                run = Run(
                    id=run_id,
                    experiment_id=experiment_id,
                    revision_id=revision.id,
                    status="QUEUED",
                    queued_at=now,
                    start_step=0,
                    requested_steps=requested_steps,
                    completed_steps=0,
                    recoverable_step=0,
                    stride_minutes=stride_minutes,
                    virtual_time=definition.simulation.start_time,
                    run_dir=paths.root.relative_to(self._var_dir).as_posix(),
                    created_at=now,
                )
                session.add(run)
                session.flush()
                session.add(RunQueue(run_id=run_id, reason="NEW", enqueued_at=now))
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="queue",
                        payload_json={"status": "QUEUED", "reason": "NEW"},
                        created_at=now,
                    )
                )
                experiment.latest_run_id = run_id
                experiment.status = "QUEUED"
                experiment.row_version += 1
                experiment.updated_at = now
        except IntegrityError as exc:
            raise ServiceError(
                "EXPERIMENT_RUN_ACTIVE",
                "该实验已有未结束运行",
                status_code=409,
            ) from exc
        return self.get_run(run_id)

    def create_from_published(
        self,
        experiment_id: str,
        revision_id: str,
        *,
        reason: str = "NEW",
    ) -> dict[str, Any]:
        now = self._now()
        try:
            with self._database.session_factory.begin() as session:
                experiment = session.get(Experiment, experiment_id)
                if experiment is None:
                    raise not_found("experiment", experiment_id)
                revision = session.get(ExperimentRevision, revision_id)
                if revision is None or revision.experiment_id != experiment_id:
                    raise not_found("revision", revision_id)
                if revision.state != "PUBLISHED":
                    raise ServiceError(
                        "REVISION_NOT_PUBLISHED",
                        "只能从已发布版本创建运行",
                        status_code=409,
                    )
                existing = session.scalar(
                    select(Run.id).where(
                        Run.experiment_id == experiment_id,
                        Run.status.in_(OPEN_RUN_STATUSES),
                    )
                )
                if existing is not None:
                    raise ServiceError(
                        "EXPERIMENT_RUN_ACTIVE",
                        "该实验已有未结束运行",
                        status_code=409,
                        details={"run_id": existing},
                    )
                definition = ExperimentDefinition.model_validate(revision.definition_json)
                requested_steps, stride_minutes = _run_shape(
                    session, revision, definition
                )
                extension_row = session.get(ExperimentRevisionCapability, revision.id)
                is_composed = (
                    extension_row is not None
                    and extension_row.extension_json.get("mode")
                    == "CAPABILITY_COMPOSED"
                )
                validation = validate_for_publish(
                    definition,
                    validate_legacy_agent_locations=not is_composed,
                )
                if not validation.valid:
                    first = validation.errors[0]
                    raise ServiceError(
                        first.code,
                        f"该实验版本不满足当前运行要求：{first.message}",
                        status_code=422,
                        details={
                            "revision_id": revision_id,
                            "errors": [
                                issue.model_dump(mode="json")
                                for issue in validation.errors
                            ],
                        },
                    )
                run_id = str(uuid4())
                paths = RunPaths.under(self._var_dir, UUID(run_id))
                relative_run_dir = paths.root.relative_to(self._var_dir).as_posix()
                run = Run(
                    id=run_id,
                    experiment_id=experiment_id,
                    revision_id=revision_id,
                    status="QUEUED",
                    queued_at=now,
                    start_step=0,
                    requested_steps=requested_steps,
                    completed_steps=0,
                    recoverable_step=0,
                    stride_minutes=stride_minutes,
                    virtual_time=definition.simulation.start_time,
                    run_dir=relative_run_dir,
                    created_at=now,
                )
                session.add(run)
                session.flush()
                session.add(
                    RunQueue(run_id=run_id, reason=reason, enqueued_at=now)
                )
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="queue",
                        payload_json={"status": "QUEUED", "reason": reason},
                        created_at=now,
                    )
                )
                experiment.latest_run_id = run_id
                experiment.status = "QUEUED"
                experiment.row_version += 1
                experiment.updated_at = now
        except IntegrityError as exc:
            raise ServiceError(
                "EXPERIMENT_RUN_ACTIVE",
                "该实验已有未结束运行",
                status_code=409,
            ) from exc
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            return self._run_detail(session, run)

    def list_runs(
        self,
        experiment_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ServiceError("INVALID_LIMIT", "limit 必须在 1 到 100 之间", status_code=422)
        with self._database.session_factory() as session:
            if session.get(Experiment, experiment_id) is None:
                raise not_found("experiment", experiment_id)
            statement = select(Run).where(Run.experiment_id == experiment_id)
            if cursor:
                created_at, cursor_id = _decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        Run.created_at < created_at,
                        and_(Run.created_at == created_at, Run.id < cursor_id),
                    )
                )
            rows = list(
                session.scalars(
                    statement.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1)
                )
            )
            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor = (
                _encode_cursor(page[-1].created_at, page[-1].id)
                if has_more and page
                else None
            )
            return {
                "items": [self._run_detail(session, run) for run in page],
                "next_cursor": next_cursor,
            }

    def pause(self, run_id: str) -> dict[str, Any]:
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status == "PAUSE_REQUESTED":
                return self._run_detail(session, run)
            if run.status != "RUNNING":
                self._invalid_transition(run, "pause")
            run.status = "PAUSE_REQUESTED"
            run.heartbeat_at = now
            self._append_state_event(session, run, now)
        return self.get_run(run_id)

    def cancel(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status == "CANCELLED":
                return self._run_detail(session, run)
            if run.status == "QUEUED":
                session.execute(delete(RunQueue).where(RunQueue.run_id == run.id))
                self._finish_without_worker(session, run, now)
            elif run.status == "PAUSED":
                self._finish_without_worker(session, run, now)
            elif run.status == "STARTING":
                if run.current_attempt_id:
                    attempt = session.get(RunAttempt, run.current_attempt_id)
                    if attempt is not None:
                        attempt.status = "ENDED"
                        attempt.ended_at = now
                        attempt.end_step = run.completed_steps
                        attempt.stop_reason = "FORCE_CANCELLED" if force else "CANCELLED"
                self._finish_without_worker(session, run, now)
            elif run.status in {"RUNNING", "PAUSE_REQUESTED"}:
                run.status = "CANCEL_REQUESTED"
                self._append_state_event(
                    session,
                    run,
                    now,
                    extra={"force": force, "supervisor_action_required": force},
                )
            elif run.status == "CANCEL_REQUESTED":
                if force:
                    # A later force request is an escalation, not an idempotent
                    # repeat of the earlier cooperative cancellation.
                    self._append_state_event(
                        session,
                        run,
                        now,
                        extra={"force": True, "supervisor_action_required": True},
                    )
            else:
                self._invalid_transition(run, "cancel")
        return self.get_run(run_id)

    def resume_paused(self, run_id: str) -> dict[str, Any]:
        # Serialize the complete recovery decision.  The rewinder itself takes
        # worker.lock then artifact.lock; recovery.lock is always acquired
        # outside that established order and is never acquired by workers.
        with self._database.session_factory() as session:
            self._require_run(session, run_id)
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        paths.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(paths.root / "recovery.lock"), timeout=30):
            return self._resume_locked(run_id)

    def _resume_locked(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            current = self._require_run(session, run_id)
            current_status = current.status
            recoverable_step = current.recoverable_step
        if current_status not in {"PAUSED", "FAILED", "INTERRUPTED"}:
            with self._database.session_factory() as session:
                self._invalid_transition(self._require_run(session, run_id), "resume")
        if recoverable_step < 1:
            raise ServiceError(
                "RUN_NOT_RECOVERABLE",
                "运行没有经过验证的可恢复检查点",
                status_code=409,
                details={"run_id": run_id, "recoverable_step": recoverable_step},
            )
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        checkpoint_reader = CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )
        try:
            checkpoint_reader.validate(
                paths.checkpoints / f"step-{recoverable_step:06d}"
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ServiceError(
                "RUN_NOT_RECOVERABLE",
                "数据库授权的检查点缺失或校验失败",
                status_code=409,
                details={
                    "run_id": run_id,
                    "recoverable_step": recoverable_step,
                    "reason": type(exc).__name__,
                },
            ) from exc
        if current_status in {"FAILED", "INTERRUPTED"}:
            from generative_agents.runtime.recovery import RunProjectionRewinder

            RunProjectionRewinder(self._database, var_dir=self._var_dir).rewind(
                run_id, recoverable_step
            )
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status not in {"PAUSED", "FAILED", "INTERRUPTED"}:
                self._invalid_transition(run, "resume")
            if run.status != current_status or run.recoverable_step != recoverable_step:
                raise ServiceError(
                    "RUN_RECOVERY_BOUNDARY_CHANGED",
                    "运行状态或可恢复边界已变化，请刷新后重试",
                    status_code=409,
                    details={"run_id": run.id, "status": run.status},
                )
            reason = "RESUME" if run.status == "PAUSED" else "RETRY"
            run.status = "QUEUED"
            run.queued_at = now
            run.finished_at = None
            run.error_code = None
            run.error_message = None
            run.resume_count += 1
            session.add(RunQueue(run_id=run.id, reason=reason, enqueued_at=now))
            self._append_state_event(session, run, now)
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = "QUEUED"
                experiment.updated_at = now
                experiment.row_version += 1
        return self.get_run(run_id)

    @staticmethod
    def _require_run(session: Session, run_id: str) -> Run:
        run = session.get(Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run

    @staticmethod
    def _invalid_transition(run: Run, action: str) -> None:
        raise ServiceError(
            "INVALID_RUN_TRANSITION",
            f"运行状态 {run.status} 不能执行 {action}",
            status_code=409,
            details={"run_id": run.id, "status": run.status, "action": action},
        )

    def _finish_without_worker(self, session: Session, run: Run, now: datetime) -> None:
        run.status = "CANCELLED"
        run.slot_no = None
        run.current_attempt_id = None
        run.pid = None
        run.pid_create_time = None
        run.heartbeat_at = now
        run.finished_at = now
        self._append_state_event(session, run, now)
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = "DRAFT" if experiment.current_draft_revision_id else "CANCELLED"
            experiment.updated_at = now
            experiment.row_version += 1

    @staticmethod
    def _append_state_event(
        session: Session,
        run: Run,
        now: datetime,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {"status": run.status}
        payload.update(extra or {})
        session.add(
            RunEvent(
                run_id=run.id,
                event_type="state",
                payload_json=payload,
                created_at=now,
            )
        )

    @staticmethod
    def _run_detail(session: Session, run: Run) -> dict[str, Any]:
        queue_position = None
        if run.status == "QUEUED":
            queue_id = session.scalar(
                select(RunQueue.id).where(RunQueue.run_id == run.id)
            )
            if queue_id is not None:
                queue_position = session.scalar(
                    select(func.count()).select_from(RunQueue).where(RunQueue.id <= queue_id)
                )
        revision = session.get(ExperimentRevision, run.revision_id)
        capability_row = session.get(ExperimentRevisionCapability, run.revision_id)
        capability_extension = (
            ExperimentCapabilityExtension.model_validate(capability_row.extension_json)
            if capability_row is not None
            else None
        )
        execution_mode = (
            capability_extension.mode if capability_extension else "LEGACY_TOWN"
        )
        result_summary = session.get(RunResultSummary, run.id)
        return {
            "run_id": run.id,
            "experiment_id": run.experiment_id,
            "revision_id": run.revision_id,
            "revision_no": revision.revision_no if revision else None,
            "definition_hash": revision.definition_hash if revision else None,
            "status": run.status,
            "queue_position": queue_position,
            "slot_no": run.slot_no,
            "requested_steps": run.requested_steps,
            "execution_mode": execution_mode,
            "step_interval_ms": (
                capability_extension.clock.snapshot_interval_ms
                if capability_extension
                and capability_extension.mode == "CAPABILITY_COMPOSED"
                else None
            ),
            "stride_minutes": run.stride_minutes,
            "completed_steps": run.completed_steps,
            "recoverable_step": run.recoverable_step,
            "available_step": result_summary.available_step if result_summary else 0,
            "virtual_time": run.virtual_time.isoformat() if run.virtual_time else None,
            "created_at": _iso_utc(run.created_at),
            "started_at": _iso_utc(run.started_at) if run.started_at else None,
            "finished_at": _iso_utc(run.finished_at) if run.finished_at else None,
            "recoverable": run.status in {"PAUSED", "FAILED", "INTERRUPTED"}
            and run.recoverable_step > 0,
        }
