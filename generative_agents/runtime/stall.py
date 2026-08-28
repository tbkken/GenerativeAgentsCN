"""Fact-based stall detection owned by the Run Supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Run,
    RunAgentStep,
    RunAttempt,
    RunDomainEvent,
    RunEvent,
)
from generative_agents.status import RunStatus


WORLD_ACTION_EVENT_TYPES = (
    "AGENT_MOVED",
    "AGENT_ACTED",
    "AGENT_WAITED",
    "AGENT_SPOKE",
    "GAME_OBJECT_INTERACTED",
)


@dataclass(frozen=True, slots=True)
class StallInspectionReport:
    """Run ids affected by one Supervisor inspection."""

    pause_requested_run_ids: tuple[str, ...]
    suspected_run_ids: tuple[str, ...]


class RunStallDetector:
    """Detect committed no-progress facts without participating in Brain logic.

    Repeated WAIT at one coordinate is a hard stall and requests a safe pause.
    Other exactly repeated stationary actions are recorded as suspicion only,
    because a repeated ACT (for example, working for an hour) can be legitimate.
    """

    def __init__(
        self,
        database: Database,
        *,
        wait_window_steps: int = 6,
        repeated_action_window_steps: int = 12,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if wait_window_steps < 2:
            raise ValueError("wait_window_steps must be at least 2")
        if repeated_action_window_steps < wait_window_steps:
            raise ValueError(
                "repeated_action_window_steps must be at least wait_window_steps"
            )
        self._database = database
        self.wait_window_steps = int(wait_window_steps)
        self.repeated_action_window_steps = int(repeated_action_window_steps)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def inspect(self) -> StallInspectionReport:
        """Inspect running Runs and persist diagnostics or a pause request."""

        paused: list[str] = []
        suspected: list[str] = []
        with self._database.session_factory.begin() as session:
            runs = list(
                session.scalars(
                    select(Run).where(Run.status == RunStatus.RUNNING.value)
                )
            )
            for run in runs:
                agent_keys = list(
                    session.scalars(
                        select(RunAgentStep.agent_key)
                        .where(RunAgentStep.run_id == run.id)
                        .distinct()
                        .order_by(RunAgentStep.agent_key)
                    )
                )
                for agent_key in agent_keys:
                    current_attempt = (
                        session.get(RunAttempt, run.current_attempt_id)
                        if run.current_attempt_id
                        else None
                    )
                    evidence = self._agent_evidence(
                        session,
                        run.id,
                        agent_key,
                        attempt_start_step=(
                            int(current_attempt.start_step)
                            if current_attempt is not None
                            else max(1, int(run.completed_steps or 0) + 1)
                        ),
                    )
                    if evidence is None:
                        continue
                    kind, payload = evidence
                    if self._already_reported(
                        session,
                        run.id,
                        kind,
                        int(payload["through_step"]),
                    ):
                        continue
                    session.add(
                        RunEvent(
                            run_id=run.id,
                            event_type=kind,
                            payload_json=payload,
                            created_at=self._now(),
                        )
                    )
                    if kind == "stall_detected":
                        run.status = RunStatus.PAUSE_REQUESTED.value
                        run.heartbeat_at = self._now()
                        session.add(
                            RunEvent(
                                run_id=run.id,
                                event_type="state",
                                payload_json={
                                    "status": RunStatus.PAUSE_REQUESTED.value,
                                    "reason": "RUN_STALL_DETECTED",
                                    "stall": payload,
                                },
                                created_at=self._now(),
                            )
                        )
                        paused.append(run.id)
                        break
                    suspected.append(run.id)
        return StallInspectionReport(
            pause_requested_run_ids=tuple(paused),
            suspected_run_ids=tuple(sorted(set(suspected))),
        )

    def _agent_evidence(
        self,
        session,
        run_id: str,
        agent_key: str,
        *,
        attempt_start_step: int,
    ):
        rows = list(
            session.scalars(
                select(RunAgentStep)
                .where(
                    RunAgentStep.run_id == run_id,
                    RunAgentStep.agent_key == agent_key,
                    # A resumed Attempt starts a fresh observation window.  Old
                    # evidence must never pause a new worker before it commits
                    # its first step.
                    RunAgentStep.step_no >= attempt_start_step,
                )
                .order_by(RunAgentStep.step_no.desc())
                .limit(self.repeated_action_window_steps)
            )
        )
        if len(rows) < self.wait_window_steps:
            return None
        expected = list(range(rows[0].step_no, rows[0].step_no - len(rows), -1))
        if [row.step_no for row in rows] != expected:
            return None
        steps = [row.step_no for row in rows]
        events = list(
            session.scalars(
                select(RunDomainEvent).where(
                    RunDomainEvent.run_id == run_id,
                    RunDomainEvent.primary_agent_key == agent_key,
                    RunDomainEvent.step_no.in_(steps),
                    RunDomainEvent.event_type.in_(WORLD_ACTION_EVENT_TYPES),
                )
            )
        )
        by_step = {event.step_no: event for event in events}
        if any(step not in by_step for step in steps):
            return None

        wait_rows = rows[: self.wait_window_steps]
        wait_events = [by_step[row.step_no] for row in wait_rows]
        wait_coord = (wait_rows[0].x, wait_rows[0].y)
        if all(
            event.event_type == "AGENT_WAITED"
            and (row.x, row.y) == wait_coord
            for row, event in zip(wait_rows, wait_events, strict=True)
        ):
            if self._bounded_wait_is_active(wait_rows, wait_events):
                return None
            wait_fingerprints = [
                self._fingerprint(row, event)
                for row, event in zip(wait_rows, wait_events, strict=True)
            ]
            # Merely remaining at one coordinate is not a hard stall.  A Brain
            # may intentionally wait through several scheduled steps.  Auto
            # pause only when the same unbounded semantic action is repeated.
            if len(set(wait_fingerprints)) == 1:
                return (
                    "stall_detected",
                    self._payload(
                        agent_key,
                        wait_rows,
                        wait_events,
                        reason="REPEATED_WAIT_AT_SAME_LOCATION",
                    ),
                )

        if len(rows) < self.repeated_action_window_steps:
            return None
        repeated_rows = rows[: self.repeated_action_window_steps]
        repeated_events = [by_step[row.step_no] for row in repeated_rows]
        fingerprints = [
            self._fingerprint(row, event)
            for row, event in zip(repeated_rows, repeated_events, strict=True)
        ]
        if len(set(fingerprints)) == 1:
            return (
                "stall_suspected",
                self._payload(
                    agent_key,
                    repeated_rows,
                    repeated_events,
                    reason="REPEATED_STATIONARY_ACTION",
                ),
            )
        return None

    @staticmethod
    def _bounded_wait_is_active(rows, events) -> bool:
        latest_step = int(rows[0].step_no)
        latest_time = rows[0].virtual_time
        for event in events:
            structured = (event.payload_json or {}).get("structured_payload") or {}
            arguments = structured.get("arguments") or {}
            expected_step = arguments.get("expected_until_step")
            if expected_step is not None:
                try:
                    if latest_step <= int(expected_step):
                        return True
                except (TypeError, ValueError):
                    pass
            expected_time = str(arguments.get("expected_until_time") or "").strip()
            if expected_time and latest_time is not None:
                try:
                    boundary = datetime.fromisoformat(expected_time.replace("Z", "+00:00"))
                    observed = latest_time
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    if boundary.tzinfo is None:
                        boundary = boundary.replace(tzinfo=timezone.utc)
                    if observed <= boundary:
                        return True
                except ValueError:
                    pass
        return False

    @staticmethod
    def _fingerprint(row: RunAgentStep, event: RunDomainEvent) -> tuple:
        payload = event.payload_json or {}
        return (
            row.x,
            row.y,
            row.action_text.strip(),
            event.event_type,
            str(payload.get("subject") or ""),
            str(payload.get("predicate") or ""),
            str(payload.get("object") or ""),
        )

    @staticmethod
    def _payload(agent_key, rows, events, *, reason: str) -> dict:
        latest_event = events[0]
        semantic = latest_event.payload_json or {}
        return {
            "reason": reason,
            "agent_key": agent_key,
            "from_step": rows[-1].step_no,
            "through_step": rows[0].step_no,
            "repeat_count": len(rows),
            "coord": [rows[0].x, rows[0].y],
            "address": rows[0].address,
            "action": rows[0].action_text,
            "event_type": latest_event.event_type,
            "event": {
                "subject": semantic.get("subject"),
                "predicate": semantic.get("predicate"),
                "object": semantic.get("object"),
            },
            "message": (
                f"Agent {agent_key} 在 step {rows[-1].step_no}-"
                f"{rows[0].step_no} 连续留在同一位置并重复同一行为"
            ),
        }

    @staticmethod
    def _already_reported(session, run_id: str, kind: str, through_step: int) -> bool:
        latest = session.scalar(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.event_type == kind)
            .order_by(RunEvent.id.desc())
            .limit(1)
        )
        return bool(
            latest
            and int((latest.payload_json or {}).get("through_step") or 0)
            >= through_step
        )


__all__ = ["RunStallDetector", "StallInspectionReport"]
