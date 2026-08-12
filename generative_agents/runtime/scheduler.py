"""Persistent FIFO scheduler transactions for local run process slots."""

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
from .artifact_contract import GENERATOR_VERSIONS


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClaimedRun:
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
    interrupted_run_ids: tuple[str, ...]
    failed_start_run_ids: tuple[str, ...]
    repaired_queue_run_ids: tuple[str, ...]


class LocalRunSchedulerRepository:
    """Keep scheduler decisions in short `BEGIN IMMEDIATE` transactions."""

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
            if run is None or run.status != "QUEUED":
                session.delete(queue_row)
                session.commit()
                return None
            attempt_no = (
                session.scalar(
                    select(func.max(RunAttempt.attempt_no)).where(RunAttempt.run_id == run.id)
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
                status="SPAWNING",
                log_path=log_path,
                start_step=start_step,
                started_at=self._now(),
            )
            session.add(attempt)
            run.status = "STARTING"
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
                        "status": "STARTING",
                        "slot_no": slot_no,
                        "attempt_id": attempt_id,
                    },
                    created_at=self._now(),
                )
            )
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = "RUNNING"
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
        """Conditionally register only the attempt currently owning the slot."""

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, claimed.run_id)
            if (
                run is None
                or run.status != "STARTING"
                or run.current_attempt_id != claimed.attempt_id
                or run.slot_no != claimed.slot_no
            ):
                return False
            attempt = session.get(RunAttempt, claimed.attempt_id)
            if attempt is None or attempt.status != "SPAWNING":
                return False
            run.status = "RUNNING"
            run.pid = pid
            run.pid_create_time = pid_create_time
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            attempt.status = "RUNNING"
            attempt.pid = pid
            attempt.pid_create_time = pid_create_time
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="state",
                    payload_json={"status": "RUNNING", "slot_no": run.slot_no},
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
        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, claimed.run_id)
            if (
                run is None
                or run.status != "STARTING"
                or run.current_attempt_id != claimed.attempt_id
            ):
                return False
            attempt = session.get(RunAttempt, claimed.attempt_id)
            if attempt is not None:
                attempt.status = "ENDED"
                attempt.ended_at = now
                attempt.end_step = run.completed_steps
                attempt.stop_reason = "START_FAILED"
                attempt.error_code = error_code
                attempt.error_message = error_message[:2000]
            self._clear_active_identity(run)
            run.status = "FAILED"
            run.finished_at = now
            run.error_code = error_code
            run.error_message = error_message[:2000]
            self._append_reconcile_event(session, run, now, "worker_start_failed")
            self._project_experiment_failed(session, run, now)
            return True

    def heartbeat(self, run_id: str, attempt_id: str) -> str | None:
        """Refresh liveness and return the durable control state for one worker."""

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, run_id)
            if (
                run is None
                or run.current_attempt_id != attempt_id
                or run.status
                not in {"RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}
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
        """Close exactly the currently-owned attempt and release its slot.

        A stale worker can never finish a newer attempt. The last fully projected
        step remains both the visible and recoverable boundary.
        """

        now = self._now()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, run_id)
            if run is None or run.current_attempt_id != attempt_id:
                return False
            attempt = session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.status == "ENDED":
                return False
            requested_status = run.status
            if requested_status == "PAUSE_REQUESTED" and exit_code == 0:
                final_status = "PAUSED"
                stop_reason = "PAUSED"
            elif requested_status == "CANCEL_REQUESTED":
                final_status = "CANCELLED"
                stop_reason = "CANCELLED" if exit_code == 0 else "FORCE_CANCELLED"
            elif exit_code == 0 and run.completed_steps >= run.requested_steps:
                final_status = "COMPLETED"
                stop_reason = "COMPLETED"
            elif exit_code == 0:
                final_status = "FAILED"
                stop_reason = "EARLY_EXIT"
            else:
                final_status = "FAILED"
                stop_reason = "WORKER_ERROR"

            attempt.status = "ENDED"
            attempt.ended_at = now
            attempt.end_step = run.completed_steps
            attempt.exit_code = exit_code
            attempt.stop_reason = stop_reason
            if final_status == "FAILED":
                attempt.error_code = error_code or "WORKER_EXITED"
                attempt.error_message = (error_message or "worker exited unexpectedly")[:2000]

            run.status = final_status
            # Projection may be newer than the last durable checkpoint. Never
            # promote an uncheckpointed step merely because the process exited.
            run.recoverable_step = min(run.recoverable_step, run.completed_steps)
            run.finished_at = now if final_status != "PAUSED" else None
            run.heartbeat_at = now
            if final_status == "FAILED":
                run.error_code = error_code or "WORKER_EXITED"
                run.error_message = (error_message or "worker exited unexpectedly")[:2000]
            self._clear_active_identity(run)
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="state",
                    payload_json={
                        "status": final_status,
                        "completed_steps": run.completed_steps,
                        "recoverable_step": run.recoverable_step,
                        "exit_code": exit_code,
                        "error_code": error_code if final_status == "FAILED" else None,
                    },
                    created_at=now,
                )
            )
            experiment = session.get(Experiment, run.experiment_id)
            if experiment is not None:
                experiment.status = (
                    "COMPLETED"
                    if final_status == "COMPLETED"
                    else "PAUSED"
                    if final_status == "PAUSED"
                    else "DRAFT"
                    if experiment.current_draft_revision_id
                    else final_status
                )
                experiment.updated_at = now
                experiment.row_version += 1
            if final_status == "COMPLETED":
                for job_type in ("BUILD_REPLAY", "BUILD_REPORT"):
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
                            ArtifactJob.status.in_({"QUEUED", "RUNNING", "SUCCEEDED"}),
                        )
                    )
                    if existing is not None:
                        continue
                    job = ArtifactJob(
                        id=str(uuid4()),
                        run_id=run.id,
                        job_type=job_type,
                        parameters_json=parameters,
                        parameters_hash=parameters_hash,
                        source_step=run.completed_steps,
                        generator_version=generator_version,
                        partial=False,
                        status="QUEUED",
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
                                "job_type": job_type,
                                "status": "QUEUED",
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
        interrupted: list[str] = []
        failed_start: list[str] = []
        repaired_queue: list[str] = []
        now = self._now()
        session = self._database.session_factory()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            active_runs = list(
                session.scalars(
                    select(Run).where(
                        Run.status.in_(
                            {"STARTING", "RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}
                        )
                    )
                )
            )
            for run in active_runs:
                attempt = (
                    session.get(RunAttempt, run.current_attempt_id)
                    if run.current_attempt_id
                    else None
                )
                if run.status == "STARTING":
                    started_at = attempt.started_at if attempt else run.created_at
                    if self._older_than(started_at, now, self.startup_timeout_seconds):
                        if attempt is not None:
                            self._end_attempt(attempt, run, now, "START_FAILED")
                        self._clear_active_identity(run)
                        run.status = "FAILED"
                        run.finished_at = now
                        run.error_code = "WORKER_START_TIMEOUT"
                        run.error_message = "worker did not register before startup timeout"
                        self._append_reconcile_event(session, run, now, "worker_start_timeout")
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
                            "FORCE_CANCELLED"
                            if run.status == "CANCEL_REQUESTED"
                            else "WEB_RECONCILE",
                        )
                    self._clear_active_identity(run)
                    cancel_requested = run.status == "CANCEL_REQUESTED"
                    run.status = "CANCELLED" if cancel_requested else "INTERRUPTED"
                    run.finished_at = now
                    if cancel_requested:
                        # A force-cancelled Run is terminal and intentionally not
                        # resumable. Fully committed frames remain readable through
                        # completed_steps; recoverable_step remains the last durable
                        # checkpoint and is never promoted by reconciliation.
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
                    select(Run).where(Run.status == "QUEUED", Run.id.not_in(queued_ids))
                )
            )
            for run in missing_queue_runs:
                session.add(RunQueue(run_id=run.id, reason="RETRY", enqueued_at=now))
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
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value + timedelta(seconds=seconds) < now

    @staticmethod
    def _default_process_identity_matches(pid: int, expected_create_time: float) -> bool:
        try:
            process = psutil.Process(pid)
            return process.is_running() and abs(process.create_time() - expected_create_time) < 0.01
        except (psutil.Error, OSError):
            return False

    @staticmethod
    def _clear_active_identity(run: Run) -> None:
        run.slot_no = None
        run.current_attempt_id = None
        run.pid = None
        run.pid_create_time = None

    @staticmethod
    def _end_attempt(
        attempt: RunAttempt,
        run: Run,
        now: datetime,
        stop_reason: str,
    ) -> None:
        attempt.status = "ENDED"
        attempt.ended_at = now
        attempt.end_step = run.completed_steps
        attempt.stop_reason = stop_reason

    @staticmethod
    def _append_reconcile_event(
        session,
        run: Run,
        now: datetime,
        reason: str,
    ) -> None:
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
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = "DRAFT" if experiment.current_draft_revision_id else "FAILED"
            experiment.updated_at = now
            experiment.row_version += 1

    @staticmethod
    def _project_experiment_cancelled(session, run: Run, now: datetime) -> None:
        experiment = session.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = (
                "DRAFT" if experiment.current_draft_revision_id else "CANCELLED"
            )
            experiment.updated_at = now
            experiment.row_version += 1
