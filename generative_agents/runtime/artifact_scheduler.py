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


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClaimedArtifactJob:
    job_id: str
    run_id: str
    attempt_no: int
    log_path: str


class ArtifactSchedulerRepository:
    def __init__(self, database: Database):
        self._database = database

    def claim_next(self) -> ClaimedArtifactJob | None:
        session = self._database.session_factory()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.scalar(
                select(ArtifactJob.id).where(ArtifactJob.status == "RUNNING").limit(1)
            ):
                session.commit()
                return None
            job = session.scalar(
                select(ArtifactJob)
                .where(ArtifactJob.status == "QUEUED")
                .order_by(ArtifactJob.created_at, ArtifactJob.id)
                .limit(1)
            )
            if job is None:
                session.commit()
                return None
            now = _now()
            job.status = "RUNNING"
            job.attempt_no += 1
            job.started_at = now
            job.finished_at = None
            job.heartbeat_at = now
            job.progress = 0
            job.error_summary = None
            job.log_path = (
                f"runs/{job.run_id}/logs/artifact-{job.id}.console.log"
            )
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

    def register(self, claimed: ClaimedArtifactJob, *, pid: int, create_time: float) -> bool:
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if (
                job is None
                or job.status != "RUNNING"
                or job.attempt_no != claimed.attempt_no
                or job.worker_pid is not None
            ):
                return False
            job.worker_pid = pid
            job.pid_create_time = create_time
            job.heartbeat_at = _now()
            return True

    def process_exited(self, claimed: ClaimedArtifactJob, exit_code: int) -> None:
        now = _now()
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if (
                job is None
                or job.status != "RUNNING"
                or job.attempt_no != claimed.attempt_no
            ):
                return
            job.status = "FAILED"
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
        now = _now()
        with self._database.session_factory.begin() as session:
            job = session.get(ArtifactJob, claimed.job_id)
            if job is None or job.status != "RUNNING":
                return
            job.status = "QUEUED" if job.attempt_no < 3 else "FAILED"
            job.worker_pid = None
            job.pid_create_time = None
            job.heartbeat_at = now
            job.error_summary = message[:2000]
            if job.status == "FAILED":
                job.finished_at = now
            session.add(
                RunEvent(
                    run_id=job.run_id,
                    event_type=(
                        "artifact_retry" if job.status == "QUEUED" else "artifact_error"
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
        repaired: list[str] = []
        now = _now()
        with self._database.session_factory.begin() as session:
            jobs = list(
                session.scalars(select(ArtifactJob).where(ArtifactJob.status == "RUNNING"))
            )
            for job in jobs:
                alive = False
                if job.worker_pid is not None and job.pid_create_time is not None:
                    try:
                        process = psutil.Process(job.worker_pid)
                        alive = process.is_running() and abs(
                            process.create_time() - job.pid_create_time
                        ) < 0.01
                    except (psutil.Error, OSError):
                        alive = False
                heartbeat = job.heartbeat_at or job.started_at or job.created_at
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                if alive or heartbeat + timedelta(seconds=startup_timeout_seconds) >= now:
                    continue
                job.status = "QUEUED" if job.attempt_no < 3 else "FAILED"
                job.worker_pid = None
                job.pid_create_time = None
                job.error_summary = "artifact worker disappeared"
                if job.status == "FAILED":
                    job.finished_at = now
                session.add(
                    RunEvent(
                        run_id=job.run_id,
                        event_type=(
                            "artifact_retry"
                            if job.status == "QUEUED"
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
    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        poll_interval_seconds: float = 1.0,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
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
        self._lock.acquire()
        self._repository.reconcile()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="artifact-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5) -> None:
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
            self._repository.spawn_failed(claimed, "invalid database-owned artifact log path")
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
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self._poll_interval)
