"""以事务方式创建运行、分页查询历史并处理运行控制状态迁移。"""

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
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Experiment,
    ExperimentRevision,
    Run,
    RunAttempt,
    RunEvent,
    RunQueue,
    RunResultSummary,
)
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.checkpoint import (
    CheckpointBundleWriter,
    CheckpointSnapshot,
)
from generative_agents.status import (
    AttemptStopReason,
    ExperimentStatus,
    OPEN_RUN_STATUSES,
    RESUMABLE_RUN_STATUSES,
    RevisionState,
    RunAttemptStatus,
    RunQueueReason,
    RunStatus,
    SLOT_OWNING_RUN_STATUSES,
)

from .errors import ServiceError, not_found

if TYPE_CHECKING:
    from .model_probes import ModelProbeService


OCCUPYING_RUN_STATUSES = SLOT_OWNING_RUN_STATUSES


def _utc_now() -> datetime:
    """执行`utc``now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """执行`iso``utc`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`datetime`。

    返回:
        返回处理后的文本或稳定标识。
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _encode_cursor(created_at: datetime, run_id: str) -> str:
    """执行`encode``cursor`的内部处理，供当前模块或类复用。

    参数:
        created_at: `created`对应的时间点。 类型：`datetime`。
        run_id: 仿真运行的唯一标识。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": run_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    """执行`decode``cursor`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`str`。

    返回:
        返回按接口约定组织的结果集合。

    异常:
        ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
    """
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
    """执行运行`shape`的内部处理，供当前模块或类复用。

    参数:
        session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
        revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`ExperimentRevision`。
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。

    返回:
        返回按接口约定组织的结果集合。
    """

    return definition.simulation.max_steps, definition.simulation.stride_minutes


class RunService:
    """管理 Run 创建、排队、查询、暂停、取消和安全恢复的事务边界。"""

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        now: Callable[[], datetime] = _utc_now,
        model_probes: ModelProbeService | None = None,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`Callable[[], datetime]`。 默认值：`_utc_now`。
            model_probes: 传入当前算法的模型`probes`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`ModelProbeService | None`。 默认值：`None`。

        返回:
            无返回值。
        """
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
        """发布`and`运行。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        from .experiments import ExperimentService

        if self._model_probes is not None:
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
                definition = ExperimentDefinition.model_validate(
                    revision.definition_json
                )
                requested_steps, stride_minutes = _run_shape(
                    session, revision, definition
                )
                paths = RunPaths.under(self._var_dir, UUID(run_id))
                run = Run(
                    id=run_id,
                    experiment_id=experiment_id,
                    revision_id=revision.id,
                    status=RunStatus.QUEUED.value,
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
                session.add(
                    RunQueue(
                        run_id=run_id,
                        reason=RunQueueReason.NEW.value,
                        enqueued_at=now,
                    )
                )
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="queue",
                        payload_json={
                            "status": RunStatus.QUEUED.value,
                            "reason": RunQueueReason.NEW.value,
                        },
                        created_at=now,
                    )
                )
                experiment.latest_run_id = run_id
                experiment.status = ExperimentStatus.QUEUED.value
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
        reason: RunQueueReason | str = RunQueueReason.NEW,
    ) -> dict[str, Any]:
        """从已发布的实验修订版本创建一条新的仿真运行。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。
            reason: 进入队列的原因。允许值：`NEW`（新运行）、`RESUME`（恢复）、`RETRY`（重试）。 类型：`RunQueueReason | str`。 默认值：`RunQueueReason.NEW`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        now = self._now()
        queue_reason = RunQueueReason(reason)
        try:
            with self._database.session_factory.begin() as session:
                experiment = session.get(Experiment, experiment_id)
                if experiment is None:
                    raise not_found("experiment", experiment_id)
                revision = session.get(ExperimentRevision, revision_id)
                if revision is None or revision.experiment_id != experiment_id:
                    raise not_found("revision", revision_id)
                if revision.state != RevisionState.PUBLISHED:
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
                definition = ExperimentDefinition.model_validate(
                    revision.definition_json
                )
                requested_steps, stride_minutes = _run_shape(
                    session, revision, definition
                )
                validation = validate_for_publish(definition)
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
                    status=RunStatus.QUEUED.value,
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
                    RunQueue(run_id=run_id, reason=queue_reason.value, enqueued_at=now)
                )
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="queue",
                        payload_json={
                            "status": RunStatus.QUEUED.value,
                            "reason": queue_reason.value,
                        },
                        created_at=now,
                    )
                )
                experiment.latest_run_id = run_id
                experiment.status = ExperimentStatus.QUEUED.value
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
        """获取运行。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        """查询`runs`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            cursor: 分页游标；为空时从结果集起点开始读取。 类型：`str | None`。 默认值：`None`。
            limit: 本次最多返回或处理的记录数量。 类型：`int`。 默认值：`50`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if limit < 1 or limit > 100:
            raise ServiceError(
                "INVALID_LIMIT", "limit 必须在 1 到 100 之间", status_code=422
            )
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
                    statement.order_by(Run.created_at.desc(), Run.id.desc()).limit(
                        limit + 1
                    )
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
        """执行 `RunService` 的`pause`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status == RunStatus.PAUSE_REQUESTED:
                return self._run_detail(session, run)
            if run.status != RunStatus.RUNNING:
                self._invalid_transition(run, "pause")
            run.status = RunStatus.PAUSE_REQUESTED.value
            run.heartbeat_at = now
            self._append_state_event(session, run, now)
        return self.get_run(run_id)

    def cancel(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        """执行 `RunService` 的`cancel`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            force: 是否忽略可安全绕过的短路条件并强制执行；不会绕过所有权或完整性校验。 类型：`bool`。 默认值：`False`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status == RunStatus.CANCELLED:
                return self._run_detail(session, run)
            if run.status == RunStatus.QUEUED:
                session.execute(delete(RunQueue).where(RunQueue.run_id == run.id))
                self._finish_without_worker(session, run, now)
            elif run.status == RunStatus.PAUSED:
                self._finish_without_worker(session, run, now)
            elif run.status == RunStatus.STARTING:
                if run.current_attempt_id:
                    attempt = session.get(RunAttempt, run.current_attempt_id)
                    if attempt is not None:
                        attempt.status = RunAttemptStatus.ENDED.value
                        attempt.ended_at = now
                        attempt.end_step = run.completed_steps
                        attempt.stop_reason = (
                            AttemptStopReason.FORCE_CANCELLED.value
                            if force
                            else AttemptStopReason.CANCELLED.value
                        )
                self._finish_without_worker(session, run, now)
            elif run.status in {RunStatus.RUNNING, RunStatus.PAUSE_REQUESTED}:
                run.status = RunStatus.CANCEL_REQUESTED.value
                self._append_state_event(
                    session,
                    run,
                    now,
                    extra={"force": force, "supervisor_action_required": force},
                )
            elif run.status == RunStatus.CANCEL_REQUESTED:
                if force:
                    # 后续强制请求属于取消升级，不是对先前协作式取消的幂等重复。
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
        # 整个恢复决策必须串行化。回退器内部依次获取 worker.lock、artifact.lock；
        # recovery.lock 始终在该顺序之外先获取，且工作进程绝不会获取它，避免锁顺序反转。
        """恢复`paused`。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self._database.session_factory() as session:
            self._require_run(session, run_id)
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        paths.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(paths.root / "recovery.lock"), timeout=30):
            return self._resume_locked(run_id)

    def _resume_locked(self, run_id: str) -> dict[str, Any]:
        """恢复`locked`。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self._database.session_factory() as session:
            current = self._require_run(session, run_id)
            current_status = current.status
            recoverable_step = current.recoverable_step
        if current_status not in RESUMABLE_RUN_STATUSES:
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
        if current_status in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
            from generative_agents.runtime.recovery import RunProjectionRewinder

            RunProjectionRewinder(self._database, var_dir=self._var_dir).rewind(
                run_id, recoverable_step
            )
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = self._require_run(session, run_id)
            if run.status not in RESUMABLE_RUN_STATUSES:
                self._invalid_transition(run, "resume")
            if run.status != current_status or run.recoverable_step != recoverable_step:
                raise ServiceError(
                    "RUN_RECOVERY_BOUNDARY_CHANGED",
                    "运行状态或可恢复边界已变化，请刷新后重试",
                    status_code=409,
                    details={"run_id": run.id, "status": run.status},
                )
            reason = (
                RunQueueReason.RESUME
                if run.status == RunStatus.PAUSED
                else RunQueueReason.RETRY
            )
            run.status = RunStatus.QUEUED.value
            run.queued_at = now
            run.finished_at = None
            run.error_code = None
            run.error_message = None
            run.resume_count += 1
            session.add(RunQueue(run_id=run.id, reason=reason.value, enqueued_at=now))
            self._append_state_event(session, run, now)
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = ExperimentStatus.QUEUED.value
                experiment.updated_at = now
                experiment.row_version += 1
        return self.get_run(run_id)

    @staticmethod
    def _require_run(session: Session, run_id: str) -> Run:
        """执行`require`运行的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回 `Run` 类型的处理结果。
        """
        run = session.get(Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run

    @staticmethod
    def _invalid_transition(run: Run, action: str) -> None:
        """执行`invalid``transition`的内部处理，供当前模块或类复用。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            action: 智能体当前选择或已经执行的行为记录。 类型：`str`。

        返回:
            无返回值。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        raise ServiceError(
            "INVALID_RUN_TRANSITION",
            f"运行状态 {run.status} 不能执行 {action}",
            status_code=409,
            details={"run_id": run.id, "status": run.status, "action": action},
        )

    def _finish_without_worker(self, session: Session, run: Run, now: datetime) -> None:
        """执行`finish``without`工作进程的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。

        返回:
            无返回值。
        """
        run.status = RunStatus.CANCELLED.value
        run.slot_no = None
        run.current_attempt_id = None
        run.pid = None
        run.pid_create_time = None
        run.heartbeat_at = now
        run.finished_at = now
        self._append_state_event(session, run, now)
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = (
                ExperimentStatus.DRAFT.value
                if experiment.current_draft_revision_id
                else ExperimentStatus.CANCELLED.value
            )
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
        """执行`append`状态事件的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。
            extra: 传入当前算法的`extra`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Any] | None`。 默认值：`None`。

        返回:
            无返回值。
        """
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
        """执行运行`detail`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        queue_position = None
        if run.status == RunStatus.QUEUED:
            queue_id = session.scalar(
                select(RunQueue.id).where(RunQueue.run_id == run.id)
            )
            if queue_id is not None:
                queue_position = session.scalar(
                    select(func.count())
                    .select_from(RunQueue)
                    .where(RunQueue.id <= queue_id)
                )
        revision = session.get(ExperimentRevision, run.revision_id)
        definition = (
            ExperimentDefinition.model_validate(revision.definition_json)
            if revision is not None
            else None
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
            "execution_mode": "SKILL_BRAIN",
            "brain_skill": (
                definition.engine.brain_skill
                if definition is not None
                else "stanford-town-brain"
            ),
            "step_interval_ms": None,
            "stride_minutes": run.stride_minutes,
            "completed_steps": run.completed_steps,
            "recoverable_step": run.recoverable_step,
            "available_step": result_summary.available_step if result_summary else 0,
            "virtual_time": run.virtual_time.isoformat() if run.virtual_time else None,
            "created_at": _iso_utc(run.created_at),
            "started_at": _iso_utc(run.started_at) if run.started_at else None,
            "finished_at": _iso_utc(run.finished_at) if run.finished_at else None,
            "recoverable": run.status in RESUMABLE_RUN_STATUSES
            and run.recoverable_step > 0,
        }
