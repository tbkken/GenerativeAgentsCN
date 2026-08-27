"""Persistent single-slot scheduler for derived result artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import psutil
from filelock import FileLock
from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import ArtifactJob, RunEvent
from generative_agents.status import ArtifactJobStatus


def _now() -> datetime:
    """执行`now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClaimedArtifactJob:
    """已经被调度器租用、可交给构建子进程的产物任务快照。"""

    job_id: str
    run_id: str
    attempt_no: int
    log_path: str


class ArtifactSchedulerRepository:
    """以数据库事务领取、续租并结束产物任务。"""

    def __init__(self, database: Database):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。

        返回:
            无返回值。
        """
        self._database = database

    def claim_next(self) -> ClaimedArtifactJob | None:
        """认领`next`。

        返回:
            返回 `ClaimedArtifactJob | None` 类型的处理结果。 没有可用结果时返回 `None`。
        """
        session = self._database.session_factory()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.scalar(
                select(ArtifactJob.id)
                .where(ArtifactJob.status == ArtifactJobStatus.RUNNING)
                .limit(1)
            ):
                session.commit()
                return None
            job = session.scalar(
                select(ArtifactJob)
                .where(ArtifactJob.status == ArtifactJobStatus.QUEUED)
                .order_by(ArtifactJob.created_at, ArtifactJob.id)
                .limit(1)
            )
            if job is None:
                session.commit()
                return None
            now = _now()
            job.status = ArtifactJobStatus.RUNNING.value
            job.attempt_no += 1
            job.started_at = now
            job.finished_at = None
            job.heartbeat_at = now
            job.progress = 0
            job.error_summary = None
            job.log_path = f"runs/{job.run_id}/logs/artifact-{job.id}.console.log"
            session.add(
                RunEvent(
                    run_id=job.run_id,
                    event_type="artifact_running",
                    payload_json={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "status": job.status,
                        "progress": job.progress,
                    },
                    created_at=now,
                )
            )
            claimed = ClaimedArtifactJob(
                job.id, job.run_id, job.attempt_no, job.log_path
            )
            session.commit()
            return claimed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register(
        self, claimed: ClaimedArtifactJob, *, pid: int, create_time: float
    ) -> bool:
        """执行 `ArtifactSchedulerRepository` 的`register`操作。

        参数:
            claimed: 调度器已经认领且绑定槽位与执行尝试的运行信息。 类型：`ClaimedArtifactJob`。
            pid: 操作系统进程标识；必须与记录的进程创建时间共同校验。 类型：`int`。
            create_time: `create`对应的时间点。 类型：`float`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if (
                job is None
                or job.status != ArtifactJobStatus.RUNNING
                or job.attempt_no != claimed.attempt_no
                or job.worker_pid is not None
            ):
                return False
            job.worker_pid = pid
            job.pid_create_time = create_time
            job.heartbeat_at = _now()
            return True

    def process_exited(self, claimed: ClaimedArtifactJob, exit_code: int) -> None:
        """执行 `ArtifactSchedulerRepository` 的`process``exited`操作。

        参数:
            claimed: 调度器已经认领且绑定槽位与执行尝试的运行信息。 类型：`ClaimedArtifactJob`。
            exit_code: 工作进程退出码；`0` 表示正常结束，非零值表示异常退出。 类型：`int`。

        返回:
            无返回值。
        """
        now = _now()
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if (
                job is None
                or job.status != ArtifactJobStatus.RUNNING
                or job.attempt_no != claimed.attempt_no
            ):
                return
            job.status = ArtifactJobStatus.FAILED.value
            job.finished_at = now
            job.worker_pid = None
            job.pid_create_time = None
            job.error_summary = f"artifact worker exited with code {exit_code}"
            session.add(
                RunEvent(
                    run_id=job.run_id,
                    event_type="artifact_error",
                    payload_json={"job_id": job.id, "error": job.error_summary},
                    created_at=now,
                )
            )

    def spawn_failed(self, claimed: ClaimedArtifactJob, message: str) -> None:
        """执行 `ArtifactSchedulerRepository` 的`spawn``failed`操作。

        参数:
            claimed: 调度器已经认领且绑定槽位与执行尝试的运行信息。 类型：`ClaimedArtifactJob`。
            message: 待发送、校验、脱敏或写入会话的消息文本或对象。 类型：`str`。

        返回:
            无返回值。
        """
        now = _now()
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if job is None or job.status != ArtifactJobStatus.RUNNING:
                return
            job.status = (
                ArtifactJobStatus.QUEUED.value
                if job.attempt_no < 3
                else ArtifactJobStatus.FAILED.value
            )
            job.worker_pid = None
            job.pid_create_time = None
            job.heartbeat_at = now
            job.error_summary = message[:2000]
            if job.status == ArtifactJobStatus.FAILED:
                job.finished_at = now
            session.add(
                RunEvent(
                    run_id=job.run_id,
                    event_type=(
                        "artifact_retry"
                        if job.status == ArtifactJobStatus.QUEUED
                        else "artifact_error"
                    ),
                    payload_json={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "status": job.status,
                        "progress": job.progress,
                        "error": job.error_summary,
                    },
                    created_at=now,
                )
            )

    def reconcile(self, *, startup_timeout_seconds: int = 60) -> tuple[str, ...]:
        """对照数据库租约与本地进程状态，修复中断后遗留的不一致状态。

        参数:
            startup_timeout_seconds: 工作进程从认领到完成注册允许的最长秒数。 类型：`int`。 默认值：`60`。

        返回:
            返回按接口约定组织的结果集合。
        """
        repaired: list[str] = []
        now = _now()
        with self._database.session_factory.begin() as session:
            jobs = list(
                session.scalars(
                    select(ArtifactJob).where(
                        ArtifactJob.status == ArtifactJobStatus.RUNNING
                    )
                )
            )
            for job in jobs:
                alive = False
                if job.worker_pid is not None and job.pid_create_time is not None:
                    try:
                        process = psutil.Process(job.worker_pid)
                        alive = (
                            process.is_running()
                            and abs(process.create_time() - job.pid_create_time) < 0.01
                        )
                    except (psutil.Error, OSError):
                        alive = False
                heartbeat = job.heartbeat_at or job.started_at or job.created_at
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                if (
                    alive
                    or heartbeat + timedelta(seconds=startup_timeout_seconds) >= now
                ):
                    continue
                job.status = (
                    ArtifactJobStatus.QUEUED.value
                    if job.attempt_no < 3
                    else ArtifactJobStatus.FAILED.value
                )
                job.worker_pid = None
                job.pid_create_time = None
                job.error_summary = "artifact worker disappeared"
                if job.status == ArtifactJobStatus.FAILED:
                    job.finished_at = now
                session.add(
                    RunEvent(
                        run_id=job.run_id,
                        event_type=(
                            "artifact_retry"
                            if job.status == ArtifactJobStatus.QUEUED
                            else "artifact_error"
                        ),
                        payload_json={
                            "job_id": job.id,
                            "job_type": job.job_type,
                            "status": job.status,
                            "progress": job.progress,
                            "error": job.error_summary,
                        },
                        created_at=now,
                    )
                )
                repaired.append(job.id)
        return tuple(repaired)


class ArtifactProcessScheduler:
    """按并发上限启动和回收本地产物构建子进程。"""

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        poll_interval_seconds: float = 1.0,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。
            poll_interval_seconds: 后台循环两次检查之间的等待秒数。 类型：`float`。 默认值：`1.0`。
            process_factory: 创建隔离工作进程的工厂；测试可注入替代实现。 类型：`Callable[..., subprocess.Popen]`。 默认值：`subprocess.Popen`。

        返回:
            无返回值。
        """
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self._repository = ArtifactSchedulerRepository(database)
        self._poll_interval = poll_interval_seconds
        self._process_factory = process_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._child: tuple[ClaimedArtifactJob, subprocess.Popen, object] | None = None
        self._lock = FileLock(str(self._var_dir / "artifact-scheduler.lock"), timeout=0)

    def start(self) -> None:
        """执行 `ArtifactProcessScheduler` 的`start`操作。

        返回:
            无返回值。
        """
        self._lock.acquire()
        self._repository.reconcile()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="artifact-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5) -> None:
        """执行 `ArtifactProcessScheduler` 的`stop`操作。

        参数:
            timeout: 等待操作的最长秒数；超时后按调用协议返回或抛出异常。 类型：`float`。 默认值：`5`。

        返回:
            无返回值。
        """
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._child:
            try:
                self._child[2].close()
            except OSError:
                pass
            self._child = None
        if self._lock.is_locked:
            self._lock.release()

    def tick(self) -> None:
        """执行一次调度循环，推进可运行任务并回收已结束进程。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        if self._child is not None:
            claimed, process, log_handle = self._child
            exit_code = process.poll()
            if exit_code is None:
                return
            log_handle.close()
            self._repository.process_exited(claimed, exit_code)
            self._child = None
        self._repository.reconcile()
        claimed = self._repository.claim_next()
        if claimed is None:
            return
        relative_log = Path(claimed.log_path)
        if relative_log.is_absolute() or ".." in relative_log.parts:
            self._repository.spawn_failed(
                claimed, "invalid database-owned artifact log path"
            )
            return
        log_path = (self._var_dir / relative_log).resolve()
        run_root = (self._var_dir / "runs" / claimed.run_id).resolve()
        if not log_path.is_relative_to(run_root) or log_path.is_symlink():
            self._repository.spawn_failed(claimed, "artifact log path escapes its run")
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab", buffering=0)
        child_env = os.environ.copy()
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = self._process_factory(
                [
                    sys.executable,
                    "-m",
                    "generative_agents.runtime.artifact_worker",
                    "--database-url",
                    str(self._database.engine.url),
                    "--var-dir",
                    str(self._var_dir),
                    "--job-id",
                    claimed.job_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parents[2]),
                shell=False,
                env=child_env,
            )
            create_time = psutil.Process(process.pid).create_time()
            if not self._repository.register(
                claimed, pid=process.pid, create_time=create_time
            ):
                process.kill()
                raise RuntimeError("artifact job ownership changed before registration")
            self._child = (claimed, process, log_handle)
        except Exception as exc:
            log_handle.close()
            self._repository.spawn_failed(claimed, f"{type(exc).__name__}: {exc}")

    def _loop(self) -> None:
        """执行`loop`的内部处理，供当前模块或类复用。

        返回:
            无返回值。
        """
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self._poll_interval)
