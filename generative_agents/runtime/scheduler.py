"""本地运行进程槽位使用的持久化 FIFO 调度事务。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import psutil
from sqlalchemy import delete, func, select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ArtifactJob,
    Experiment,
    Run,
    RunAttempt,
    RunEvent,
    RunQueue,
)
from generative_agents.config import canonical_json_bytes
from generative_agents.status import (
    ACTIVE_ARTIFACT_JOB_STATUSES,
    ArtifactJobStatus,
    ArtifactJobType,
    AttemptStopReason,
    ExperimentStatus,
    RunAttemptStatus,
    RunQueueReason,
    RunStatus,
    SLOT_OWNING_RUN_STATUSES,
    WORKER_OWNED_RUN_STATUSES,
)
from .artifact_contract import GENERATOR_VERSIONS


def _utc_now() -> datetime:
    """执行`utc``now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """已由当前 Supervisor 租用的队列项及其新 Attempt 身份。"""

    run_id: str
    experiment_id: str
    revision_id: str
    attempt_id: str
    attempt_no: int
    slot_no: int
    start_step: int
    log_path: str


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """一次生命周期对账中修复、终止或释放的 Run 数量摘要。"""

    interrupted_run_ids: tuple[str, ...]
    failed_start_run_ids: tuple[str, ...]
    repaired_queue_run_ids: tuple[str, ...]


class LocalRunSchedulerRepository:
    """在短小的 `BEGIN IMMEDIATE` 事务内完成调度决策。"""

    def __init__(
        self,
        database: Database,
        *,
        max_concurrent_runs: int = 2,
        startup_timeout_seconds: int = 60,
        heartbeat_timeout_seconds: int = 30,
        now: Callable[[], datetime] = _utc_now,
        process_identity_matches: Callable[[int, float], bool] | None = None,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            max_concurrent_runs: `concurrent``runs`允许的最大值。 类型：`int`。 默认值：`2`。
            startup_timeout_seconds: 工作进程从认领到完成注册允许的最长秒数。 类型：`int`。 默认值：`60`。
            heartbeat_timeout_seconds: 工作进程心跳超过该秒数后视为租约失效。 类型：`int`。 默认值：`30`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`Callable[[], datetime]`。 默认值：`_utc_now`。
            process_identity_matches: 校验 PID 与进程创建时间是否仍属于原工作进程的函数。 类型：`Callable[[int, float], bool] | None`。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be positive")
        self._database = database
        self.max_concurrent_runs = max_concurrent_runs
        self.startup_timeout_seconds = startup_timeout_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._now = now
        self._process_identity_matches = (
            process_identity_matches or self._default_process_identity_matches
        )

    def claim_next(self) -> ClaimedRun | None:
        """认领`next`。

        返回:
            返回 `ClaimedRun | None` 类型的处理结果。 没有可用结果时返回 `None`。
        """
        session = self._database.session_factory()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            occupied = set(
                session.scalars(select(Run.slot_no).where(Run.slot_no.is_not(None)))
            )
            slot_no = next(
                (
                    candidate
                    for candidate in range(1, self.max_concurrent_runs + 1)
                    if candidate not in occupied
                ),
                None,
            )
            if slot_no is None:
                session.commit()
                return None
            queue_row = session.scalar(select(RunQueue).order_by(RunQueue.id).limit(1))
            if queue_row is None:
                session.commit()
                return None
            run = session.get(Run, queue_row.run_id)
            if run is None or run.status != RunStatus.QUEUED:
                session.delete(queue_row)
                session.commit()
                return None
            attempt_no = (
                session.scalar(
                    select(func.max(RunAttempt.attempt_no)).where(
                        RunAttempt.run_id == run.id
                    )
                )
                or 0
            ) + 1
            attempt_id = str(uuid4())
            start_step = max(run.start_step, run.recoverable_step) + 1
            log_path = f"runs/{run.id}/logs/attempt-{attempt_no:03d}.console.log"
            attempt = RunAttempt(
                id=attempt_id,
                run_id=run.id,
                attempt_no=attempt_no,
                slot_no=slot_no,
                status=RunAttemptStatus.SPAWNING.value,
                log_path=log_path,
                start_step=start_step,
                started_at=self._now(),
            )
            session.add(attempt)
            run.status = RunStatus.STARTING.value
            run.slot_no = slot_no
            run.current_attempt_id = attempt_id
            run.pid = None
            run.pid_create_time = None
            session.delete(queue_row)
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="state",
                    payload_json={
                        "status": RunStatus.STARTING.value,
                        "slot_no": slot_no,
                        "attempt_id": attempt_id,
                    },
                    created_at=self._now(),
                )
            )
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = ExperimentStatus.RUNNING.value
                experiment.updated_at = self._now()
                experiment.row_version += 1
            claimed = ClaimedRun(
                run_id=run.id,
                experiment_id=run.experiment_id,
                revision_id=run.revision_id,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                slot_no=slot_no,
                start_step=start_step,
                log_path=log_path,
            )
            session.commit()
            return claimed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_worker(
        self,
        claimed: ClaimedRun,
        *,
        pid: int,
        pid_create_time: float,
    ) -> bool:
        """执行 `LocalRunSchedulerRepository` 的`register`工作进程操作。

        参数:
            claimed: 调度器已经认领且绑定槽位与执行尝试的运行信息。 类型：`ClaimedRun`。
            pid: 操作系统进程标识；必须与记录的进程创建时间共同校验。 类型：`int`。
            pid_create_time: `pid``create`对应的时间点。 类型：`float`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, claimed.run_id)
            if (
                run is None
                or run.status != RunStatus.STARTING
                or run.current_attempt_id != claimed.attempt_id
                or run.slot_no != claimed.slot_no
            ):
                return False
            attempt = session.get(RunAttempt, claimed.attempt_id)
            if attempt is None or attempt.status != RunAttemptStatus.SPAWNING:
                return False
            run.status = RunStatus.RUNNING.value
            run.pid = pid
            run.pid_create_time = pid_create_time
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            attempt.status = RunAttemptStatus.RUNNING.value
            attempt.pid = pid
            attempt.pid_create_time = pid_create_time
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="state",
                    payload_json={
                        "status": RunStatus.RUNNING.value,
                        "slot_no": run.slot_no,
                    },
                    created_at=now,
                )
            )
            return True

    def mark_spawn_failed(
        self,
        claimed: ClaimedRun,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        """执行 `LocalRunSchedulerRepository` 的`mark``spawn``failed`操作。

        参数:
            claimed: 调度器已经认领且绑定槽位与执行尝试的运行信息。 类型：`ClaimedRun`。
            error_code: 工作进程失败时记录的稳定错误码；正常结束时为 `None`。 类型：`str`。
            error_message: 工作进程失败时记录的可读错误信息；正常结束时为 `None`。 类型：`str`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, claimed.run_id)
            if (
                run is None
                or run.status != RunStatus.STARTING
                or run.current_attempt_id != claimed.attempt_id
            ):
                return False
            attempt = session.get(RunAttempt, claimed.attempt_id)
            if attempt is not None:
                attempt.status = RunAttemptStatus.ENDED.value
                attempt.ended_at = now
                attempt.end_step = run.completed_steps
                attempt.stop_reason = AttemptStopReason.START_FAILED.value
                attempt.error_code = error_code
                attempt.error_message = error_message[:2000]
            self._clear_active_identity(run)
            run.status = RunStatus.FAILED.value
            run.finished_at = now
            run.error_code = error_code
            run.error_message = error_message[:2000]
            self._append_reconcile_event(session, run, now, "worker_start_failed")
            self._project_experiment_failed(session, run, now)
            return True

    def heartbeat(self, run_id: str, attempt_id: str) -> str | None:
        """执行 `LocalRunSchedulerRepository` 的`heartbeat`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。 没有可用结果时返回 `None`。
        """

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, run_id)
            if (
                run is None
                or run.current_attempt_id != attempt_id
                or run.status not in WORKER_OWNED_RUN_STATUSES
            ):
                return None
            run.heartbeat_at = now
            return run.status

    def finish_worker(
        self,
        run_id: str,
        attempt_id: str,
        *,
        exit_code: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """结束当前工作进程实际拥有的执行尝试，并释放对应并发槽位。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。
            exit_code: 工作进程退出码；`0` 表示正常结束，非零值表示异常退出。 类型：`int`。
            error_code: 工作进程失败时记录的稳定错误码；正常结束时为 `None`。 类型：`str | None`。 默认值：`None`。
            error_message: 工作进程失败时记录的可读错误信息；正常结束时为 `None`。 类型：`str | None`。 默认值：`None`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。

        说明:
            attempt_id 与 worker_token 共同构成所有权栅栏。只有当前所有者能结束尝试；状态更新、槽位释放和产物任务创建必须处于同一事务。
        """

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, run_id)
            if run is None or run.current_attempt_id != attempt_id:
                return False
            attempt = session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.status == RunAttemptStatus.ENDED:
                return False
            requested_status = run.status
            if requested_status == RunStatus.PAUSE_REQUESTED and exit_code == 0:
                final_status = RunStatus.PAUSED
                stop_reason = AttemptStopReason.PAUSED
            elif requested_status == RunStatus.CANCEL_REQUESTED:
                final_status = RunStatus.CANCELLED
                stop_reason = (
                    AttemptStopReason.CANCELLED
                    if exit_code == 0
                    else AttemptStopReason.FORCE_CANCELLED
                )
            elif exit_code == 0 and run.completed_steps >= run.requested_steps:
                final_status = RunStatus.COMPLETED
                stop_reason = AttemptStopReason.COMPLETED
            elif exit_code == 0:
                final_status = RunStatus.FAILED
                stop_reason = AttemptStopReason.EARLY_EXIT
            else:
                final_status = RunStatus.FAILED
                stop_reason = AttemptStopReason.WORKER_ERROR

            attempt.status = RunAttemptStatus.ENDED.value
            attempt.ended_at = now
            attempt.end_step = run.completed_steps
            attempt.exit_code = exit_code
            attempt.stop_reason = stop_reason.value
            if final_status == RunStatus.FAILED:
                attempt.error_code = error_code or "WORKER_EXITED"
                attempt.error_message = (error_message or "worker exited unexpectedly")[
                    :2000
                ]

            run.status = final_status.value
            # 查询投影可能领先于最后一个持久化检查点。进程退出本身不能把尚未
            # 进入检查点的步骤提升为恢复边界，否则重试会从不完整状态继续。
            run.recoverable_step = min(run.recoverable_step, run.completed_steps)
            run.finished_at = now if final_status != RunStatus.PAUSED else None
            run.heartbeat_at = now
            if final_status == RunStatus.FAILED:
                run.error_code = error_code or "WORKER_EXITED"
                run.error_message = (error_message or "worker exited unexpectedly")[
                    :2000
                ]
            self._clear_active_identity(run)
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="state",
                    payload_json={
                        "status": final_status.value,
                        "completed_steps": run.completed_steps,
                        "recoverable_step": run.recoverable_step,
                        "exit_code": exit_code,
                        "error_code": (
                            error_code if final_status == RunStatus.FAILED else None
                        ),
                    },
                    created_at=now,
                )
            )
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = (
                    ExperimentStatus.COMPLETED.value
                    if final_status == RunStatus.COMPLETED
                    else ExperimentStatus.PAUSED.value
                    if final_status == RunStatus.PAUSED
                    else ExperimentStatus.DRAFT.value
                    if experiment.current_draft_revision_id
                    else final_status.value
                )
                experiment.updated_at = now
                experiment.row_version += 1
            if final_status == RunStatus.COMPLETED:
                for job_type in (
                    ArtifactJobType.BUILD_REPLAY,
                    ArtifactJobType.BUILD_REPORT,
                ):
                    generator_version = GENERATOR_VERSIONS[job_type]
                    parameters = {
                        "source_step": run.completed_steps,
                        "generator_version": generator_version,
                        "partial": False,
                    }
                    parameters_hash = hashlib.sha256(
                        canonical_json_bytes(parameters)
                    ).hexdigest()
                    existing = session.scalar(
                        select(ArtifactJob.id).where(
                            ArtifactJob.run_id == run.id,
                            ArtifactJob.job_type == job_type,
                            ArtifactJob.parameters_hash == parameters_hash,
                            ArtifactJob.source_step == run.completed_steps,
                            ArtifactJob.generator_version == generator_version,
                            ArtifactJob.status.in_(
                                {
                                    *ACTIVE_ARTIFACT_JOB_STATUSES,
                                    ArtifactJobStatus.SUCCEEDED,
                                }
                            ),
                        )
                    )
                    if existing is not None:
                        continue
                    job = ArtifactJob(
                        id=str(uuid4()),
                        run_id=run.id,
                        job_type=job_type.value,
                        parameters_json=parameters,
                        parameters_hash=parameters_hash,
                        source_step=run.completed_steps,
                        generator_version=generator_version,
                        partial=False,
                        status=ArtifactJobStatus.QUEUED.value,
                        attempt_no=0,
                        progress=0,
                        created_at=now,
                    )
                    session.add(job)
                    session.add(
                        RunEvent(
                            run_id=run.id,
                            event_type="artifact_queued",
                            payload_json={
                                "job_id": job.id,
                                "job_type": job_type.value,
                                "status": ArtifactJobStatus.QUEUED.value,
                                "progress": 0,
                                "source_step": run.completed_steps,
                                "generator_version": generator_version,
                                "partial": False,
                            },
                            created_at=now,
                        )
                    )
            return True

    def reconcile(self) -> ReconcileReport:
        """对照数据库租约与本地进程状态，修复中断后遗留的不一致状态。

        返回:
            返回 `ReconcileReport` 类型的处理结果。

        说明:
            恢复逻辑以持久化租约为事实来源；本地进程消失或租约过期时，只能通过受控状态迁移收敛。
        """
        interrupted: list[str] = []
        failed_start: list[str] = []
        repaired_queue: list[str] = []
        now = self._now()
        session = self._database.session_factory()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            active_runs = list(
                session.scalars(
                    select(Run).where(Run.status.in_(SLOT_OWNING_RUN_STATUSES))
                )
            )
            for run in active_runs:
                attempt = (
                    session.get(RunAttempt, run.current_attempt_id)
                    if run.current_attempt_id
                    else None
                )
                if run.status == RunStatus.STARTING:
                    started_at = attempt.started_at if attempt else run.created_at
                    if self._older_than(started_at, now, self.startup_timeout_seconds):
                        if attempt is not None:
                            self._end_attempt(
                                attempt,
                                run,
                                now,
                                AttemptStopReason.START_FAILED,
                            )
                        self._clear_active_identity(run)
                        run.status = RunStatus.FAILED.value
                        run.finished_at = now
                        run.error_code = "WORKER_START_TIMEOUT"
                        run.error_message = (
                            "worker did not register before startup timeout"
                        )
                        self._append_reconcile_event(
                            session, run, now, "worker_start_timeout"
                        )
                        self._project_experiment_failed(session, run, now)
                        failed_start.append(run.id)
                    continue
                heartbeat_stale = run.heartbeat_at is None or self._older_than(
                    run.heartbeat_at, now, self.heartbeat_timeout_seconds
                )
                process_missing = (
                    run.pid is None
                    or run.pid_create_time is None
                    or not self._process_identity_matches(run.pid, run.pid_create_time)
                )
                if process_missing or heartbeat_stale:
                    if attempt is not None:
                        self._end_attempt(
                            attempt,
                            run,
                            now,
                            AttemptStopReason.FORCE_CANCELLED
                            if run.status == RunStatus.CANCEL_REQUESTED
                            else AttemptStopReason.WEB_RECONCILE,
                        )
                    self._clear_active_identity(run)
                    cancel_requested = run.status == RunStatus.CANCEL_REQUESTED
                    run.status = (
                        RunStatus.CANCELLED.value
                        if cancel_requested
                        else RunStatus.INTERRUPTED.value
                    )
                    run.finished_at = now
                    if cancel_requested:
                        # 强制取消后的 Run 是不可恢复终态。已完整提交的 Frame 仍可读取到
                        # completed_steps，但 recoverable_step 只表示最后一个可靠检查点；
                        # 对账不能因为进程已经退出就擅自提升恢复边界。
                        run.error_code = None
                        run.error_message = None
                    else:
                        run.error_code = (
                            "WORKER_DISAPPEARED"
                            if process_missing
                            else "WORKER_HEARTBEAT_TIMEOUT"
                        )
                        run.error_message = (
                            "worker process identity is no longer alive"
                            if process_missing
                            else "worker heartbeat exceeded its liveness deadline"
                        )
                    self._append_reconcile_event(
                        session,
                        run,
                        now,
                        "force_cancel_completed"
                        if cancel_requested
                        else "worker_disappeared"
                        if process_missing
                        else "worker_heartbeat_timeout",
                    )
                    if cancel_requested:
                        self._project_experiment_cancelled(session, run, now)
                    else:
                        self._project_experiment_failed(session, run, now)
                        interrupted.append(run.id)

            queued_ids = set(session.scalars(select(RunQueue.run_id)))
            missing_queue_runs = list(
                session.scalars(
                    select(Run).where(
                        Run.status == RunStatus.QUEUED, Run.id.not_in(queued_ids)
                    )
                )
            )
            for run in missing_queue_runs:
                session.add(
                    RunQueue(
                        run_id=run.id,
                        reason=RunQueueReason.RETRY.value,
                        enqueued_at=now,
                    )
                )
                self._append_reconcile_event(session, run, now, "queue_row_repaired")
                repaired_queue.append(run.id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return ReconcileReport(
            interrupted_run_ids=tuple(sorted(interrupted)),
            failed_start_run_ids=tuple(sorted(failed_start)),
            repaired_queue_run_ids=tuple(sorted(repaired_queue)),
        )

    @staticmethod
    def _older_than(value: datetime, now: datetime, seconds: int) -> bool:
        """执行`older``than`的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`datetime`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。
            seconds: 超时、等待或租约计算使用的秒数。 类型：`int`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value + timedelta(seconds=seconds) < now

    @staticmethod
    def _default_process_identity_matches(
        pid: int, expected_create_time: float
    ) -> bool:
        """执行`default``process``identity``matches`的内部处理，供当前模块或类复用。

        参数:
            pid: 操作系统进程标识；必须与记录的进程创建时间共同校验。 类型：`int`。
            expected_create_time: `expected``create`对应的时间点。 类型：`float`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        try:
            process = psutil.Process(pid)
            return (
                process.is_running()
                and abs(process.create_time() - expected_create_time) < 0.01
            )
        except (psutil.Error, OSError):
            return False

    @staticmethod
    def _clear_active_identity(run: Run) -> None:
        """执行`clear``active``identity`的内部处理，供当前模块或类复用。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。

        返回:
            无返回值。
        """
        run.slot_no = None
        run.current_attempt_id = None
        run.pid = None
        run.pid_create_time = None

    @staticmethod
    def _end_attempt(
        attempt: RunAttempt,
        run: Run,
        now: datetime,
        stop_reason: AttemptStopReason | str,
    ) -> None:
        """执行`end`执行尝试的内部处理，供当前模块或类复用。

        参数:
            attempt: 当前运行的执行尝试记录。 类型：`RunAttempt`。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。
            stop_reason: 执行尝试结束原因；允许值由 `AttemptStopReason` 定义。 类型：`AttemptStopReason | str`。

        返回:
            无返回值。
        """
        attempt.status = RunAttemptStatus.ENDED.value
        attempt.ended_at = now
        attempt.end_step = run.completed_steps
        attempt.stop_reason = AttemptStopReason(stop_reason).value

    @staticmethod
    def _append_reconcile_event(
        session,
        run: Run,
        now: datetime,
        reason: str,
    ) -> None:
        """执行`append``reconcile`事件的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。
            reason: 执行尝试停止原因。允许值：`COMPLETED`、`PAUSED`、`CANCELLED`、`FORCE_CANCELLED`、`START_FAILED`、`EARLY_EXIT`、`WORKER_ERROR`、`WEB_RECONCILE`。 类型：`str`。

        返回:
            无返回值。
        """
        session.add(
            RunEvent(
                run_id=run.id,
                event_type="reconcile",
                payload_json={"status": run.status, "reason": reason},
                created_at=now,
            )
        )

    @staticmethod
    def _project_experiment_failed(session, run: Run, now: datetime) -> None:
        """执行`project`实验`failed`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。

        返回:
            无返回值。
        """
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = (
                ExperimentStatus.DRAFT.value
                if experiment.current_draft_revision_id
                else ExperimentStatus.FAILED.value
            )
            experiment.updated_at = now
            experiment.row_version += 1

    @staticmethod
    def _project_experiment_cancelled(session, run: Run, now: datetime) -> None:
        """执行`project`实验`cancelled`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            now: 本次操作采用的基准时间；传入后可保证事务内时间判断一致。 类型：`datetime`。

        返回:
            无返回值。
        """
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = (
                ExperimentStatus.DRAFT.value
                if experiment.current_draft_revision_id
                else ExperimentStatus.CANCELLED.value
            )
            experiment.updated_at = now
            experiment.row_version += 1
