"""Idempotently project complete StepResult frames into SQLite query tables."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunAttempt,
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

from .frame_store import StoredFrame
from .results import ActivityKind, MemoryDeltaKind, StepResult


class ResultProjectionError(RuntimeError):
    pass


class SqliteResultProjector:
    """The final short transaction in the frame/checkpoint/projection chain."""

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        projection_version: str = "ga-result-v1",
    ):
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self.projection_version = projection_version

    def commit_step(
        self,
        result: StepResult,
        *,
        frame: StoredFrame,
        checkpoint_path: Path | None,
        allow_reconcile: bool = False,
    ) -> int:
        now = datetime.now(timezone.utc)
        frame_path = frame.path.resolve()
        if not frame_path.is_relative_to(self._var_dir):
            raise ResultProjectionError("frame path is outside configured var_dir")
        relative_frame = frame_path.relative_to(self._var_dir).as_posix()
        with self._database.session_factory.begin() as session:
            run = session.get(Run, str(result.run_id))
            if run is None:
                raise ResultProjectionError("run does not exist")
            if not allow_reconcile and (
                run.current_attempt_id != str(result.attempt_id)
                or run.status not in {"RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}
            ):
                raise ResultProjectionError("stale attempt cannot project a result step")
            summary = session.get(RunResultSummary, run.id)
            if summary is None:
                summary = RunResultSummary(
                    run_id=run.id,
                    available_step=0,
                    result_state="EMPTY",
                    capabilities_json=self._default_capabilities(),
                    projection_version=self.projection_version,
                    result_version=0,
                    updated_at=now,
                )
                session.add(summary)
                session.flush()
            existing = session.get(RunStep, (run.id, result.step_no))
            if existing is not None:
                if existing.frame_sha256 != frame.sha256:
                    raise ResultProjectionError(
                        "step already projected with a different frame hash"
                    )
                return summary.result_version
            if result.step_no != summary.available_step + 1:
                raise ResultProjectionError(
                    f"projection gap: available={summary.available_step}, incoming={result.step_no}"
                )
            revision = session.get(ExperimentRevision, run.revision_id)
            if revision is None:
                raise ResultProjectionError("run revision does not exist")
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            logical_calls = len(result.committed_model_usage)
            retries = sum(
                max(0, usage.physical_attempts - 1)
                for usage in result.committed_model_usage
            )
            message_count = sum(
                len(conversation.messages) for conversation in result.conversations
            )
            memory_created = sum(
                delta.kind == MemoryDeltaKind.CREATED for delta in result.memory_deltas
            )
            memory_accessed = sum(
                delta.kind == MemoryDeltaKind.ACCESSED for delta in result.memory_deltas
            )
            movement_count = sum(
                agent.from_coord != agent.to_coord or len(agent.path) > 1
                for agent in result.agents
            )
            session.add(
                RunStep(
                    run_id=run.id,
                    step_no=result.step_no,
                    attempt_id=str(result.attempt_id),
                    virtual_time=result.virtual_time,
                    frame_path=relative_frame,
                    frame_sha256=frame.sha256,
                    action_count=len(result.agents),
                    movement_count=movement_count,
                    conversation_count=len(result.conversations),
                    message_count=message_count,
                    memory_created_count=memory_created,
                    memory_accessed_count=memory_accessed,
                    model_logical_calls=logical_calls,
                    model_retry_count=retries,
                    active_agent_count=len(result.agents),
                    checkpoint=checkpoint_path is not None,
                    committed_at=now,
                )
            )
            session.flush()
            forced_agent_keys = {
                key
                for conversation in result.conversations
                for key in conversation.participant_agent_keys
            }
            forced_agent_keys.update(delta.agent_key for delta in result.memory_deltas)
            forced_agent_keys.update(item.agent_key for item in result.schedule_revisions)
            forced_agent_keys.update(
                key for event in result.domain_events for key in event.agent_keys
            )
            project_all_agents = (
                result.step_no
                % definition.results.agent_step_projection_interval_steps
                == 0
                or result.step_no >= run.requested_steps
            )
            for agent in result.agents:
                if project_all_agents or agent.agent_key in forced_agent_keys:
                    session.add(
                        RunAgentStep(
                            run_id=run.id,
                            step_no=result.step_no,
                            agent_key=agent.agent_key,
                            virtual_time=result.virtual_time,
                            x=agent.to_coord[0],
                            y=agent.to_coord[1],
                            address=" / ".join(agent.location),
                            action_text=agent.action.description,
                            action_emoji=agent.action.emoji,
                            activity_kind=agent.activity_kind.value,
                            currently_text=agent.currently,
                            schedule_item_id=agent.schedule_item_id,
                            path_source=agent.path_source,
                            decision_context_json=dict(agent.decision_context),
                        )
                    )
                agent_summary = self._get_agent_summary(
                    session, run.id, agent.agent_key, agent
                )
                agent_summary.x, agent_summary.y = agent.to_coord
                agent_summary.address = " / ".join(agent.location)
                agent_summary.currently_text = agent.currently
                agent_summary.action_count += 1
                agent_summary.movement_steps += max(0, len(agent.path) - 1)
                duration_field = {
                    ActivityKind.REST: "rest_minutes",
                    ActivityKind.CHAT: "chat_minutes",
                    ActivityKind.MOVING: "moving_minutes",
                    ActivityKind.OTHER: "other_minutes",
                }[agent.activity_kind]
                setattr(
                    agent_summary,
                    duration_field,
                    getattr(agent_summary, duration_field) + run.stride_minutes,
                )
                agent_summary.updated_step = result.step_no
            session.flush()

            for conversation in result.conversations:
                self._conversation(session, run, result, conversation)
            for delta in result.memory_deltas:
                self._memory(session, run.id, result, delta)
            for schedule in result.schedule_revisions:
                self._schedule(session, run.id, result, schedule)
            for event in result.domain_events:
                payload = dict(event.payload)
                session.add(
                    RunDomainEvent(
                        id=str(event.event_id),
                        run_id=run.id,
                        step_no=result.step_no,
                        virtual_time=result.virtual_time,
                        event_type=event.event_type,
                        primary_agent_key=(event.agent_keys[0] if event.agent_keys else None),
                        title=str(payload.get("title") or event.event_type),
                        detail=payload.get("detail"),
                        location=payload.get("location"),
                        importance_score=float(payload.get("importance_score", 0)),
                        source_type=payload.get("source_type"),
                        source_id=payload.get("source_id"),
                        payload_json=payload,
                    )
                )
                for agent_key in sorted(set(event.agent_keys)):
                    session.add(
                        RunDomainEventAgent(
                            run_id=run.id,
                            event_id=str(event.event_id),
                            agent_key=agent_key,
                        )
                    )

            summary.available_step = result.step_no
            summary.virtual_time = result.virtual_time
            summary.action_count += len(result.agents)
            summary.conversation_count += len(result.conversations)
            summary.message_count += message_count
            summary.memory_count += memory_created
            summary.model_call_count += logical_calls
            summary.model_retry_count += retries
            summary.result_state = (
                "COMPLETE" if result.step_no >= run.requested_steps else "PARTIAL"
            )
            summary.projection_version = self.projection_version
            summary.result_version += 1
            summary.last_frame_sha256 = frame.sha256
            summary.updated_at = now
            run.completed_steps = result.step_no
            run.virtual_time = result.virtual_time
            run.heartbeat_at = now
            if checkpoint_path is not None:
                run.recoverable_step = result.step_no
            attempt = session.get(RunAttempt, str(result.attempt_id))
            if attempt is not None:
                attempt.end_step = result.step_no
            session.add(
                RunEvent(
                    run_id=run.id,
                    event_type="progress",
                    payload_json={
                        "completed_steps": run.completed_steps,
                        "available_step": summary.available_step,
                        "recoverable_step": run.recoverable_step,
                        "result_version": summary.result_version,
                        "virtual_time": result.virtual_time.isoformat(),
                    },
                    created_at=now,
                )
            )
            return summary.result_version

    @staticmethod
    def _get_agent_summary(session, run_id, agent_key, agent) -> RunAgentSummary:
        value = session.get(RunAgentSummary, (run_id, agent_key))
        if value is None:
            value = RunAgentSummary(
                run_id=run_id,
                agent_key=agent_key,
                x=agent.to_coord[0],
                y=agent.to_coord[1],
                address=" / ".join(agent.location),
                currently_text=agent.currently,
                action_count=0,
                movement_steps=0,
                conversation_count=0,
                message_count=0,
                memory_created_count=0,
                rest_minutes=0,
                chat_minutes=0,
                moving_minutes=0,
                other_minutes=0,
                updated_step=0,
            )
            session.add(value)
        return value

    @staticmethod
    def _conversation(session, run, result, conversation) -> None:
        initiator, responder = conversation.participant_agent_keys
        participants = tuple(sorted((initiator, responder)))
        if participants[0] == participants[1]:
            raise ResultProjectionError("conversation requires two distinct participants")
        session.add(
            RunConversation(
                id=str(conversation.conversation_id),
                run_id=run.id,
                start_step=result.step_no,
                end_step=result.step_no,
                started_at=result.virtual_time,
                ended_at=result.virtual_time,
                duration_minutes=conversation.duration_minutes,
                duration_source=conversation.duration_source,
                location=" / ".join(conversation.location),
                initiator_agent_key=initiator,
                responder_agent_key=responder,
                message_count=len(conversation.messages),
                summary=conversation.summary,
                ended_reason=conversation.ended_reason,
            )
        )
        for agent_key in participants:
            session.add(
                RunConversationParticipant(
                    run_id=run.id,
                    conversation_id=str(conversation.conversation_id),
                    agent_key=agent_key,
                )
            )
        for message in conversation.messages:
            session.add(
                RunMessage(
                    id=str(message.message_id),
                    run_id=run.id,
                    conversation_id=str(conversation.conversation_id),
                    sequence_no=message.sequence,
                    speaker_agent_key=message.speaker_agent_key,
                    content=message.content,
                    observed_at=result.virtual_time,
                    source_step=result.step_no,
                )
            )
        edge = session.get(
            RunRelationshipEdge, (run.id, participants[0], participants[1])
        )
        if edge is None:
            edge = RunRelationshipEdge(
                run_id=run.id,
                agent_a=participants[0],
                agent_b=participants[1],
                conversation_count=0,
                message_count=0,
                duration_minutes=0,
                first_conversation_at=result.virtual_time,
                last_conversation_at=result.virtual_time,
            )
            session.add(edge)
        edge.conversation_count += 1
        edge.message_count += len(conversation.messages)
        edge.duration_minutes += conversation.duration_minutes or 0
        edge.last_conversation_at = result.virtual_time
        for agent_key in participants:
            agent_summary = session.get(RunAgentSummary, (run.id, agent_key))
            if agent_summary is not None:
                agent_summary.conversation_count += 1
                agent_summary.message_count += len(conversation.messages)

    @staticmethod
    def _memory(session, run_id, result, delta) -> None:
        memory = session.get(
            RunMemoryEvent, (run_id, delta.agent_key, delta.memory_id)
        )
        if delta.kind == MemoryDeltaKind.CREATED:
            if memory is None:
                session.add(
                    RunMemoryEvent(
                        run_id=run_id,
                        agent_key=delta.agent_key,
                        memory_node_id=delta.memory_id,
                        memory_type=delta.memory_type,
                        origin="RUN",
                        state="ACTIVE",
                        description=delta.description,
                        poignancy=delta.poignancy,
                        created_step=result.step_no,
                        created_at=result.virtual_time,
                        last_accessed_step=result.step_no,
                        last_accessed_at=result.virtual_time,
                        evidence_node_ids_json=[],
                    )
                )
            agent_summary = session.get(RunAgentSummary, (run_id, delta.agent_key))
            if agent_summary is not None:
                agent_summary.memory_created_count += 1
            return
        if memory is None:
            raise ResultProjectionError(
                f"memory delta {delta.kind.value} references unknown node {delta.memory_id}"
            )
        if delta.kind == MemoryDeltaKind.ACCESSED:
            memory.last_accessed_step = result.step_no
            memory.last_accessed_at = result.virtual_time
        else:
            memory.state = (
                "EXPIRED" if delta.kind == MemoryDeltaKind.EXPIRED else "EVICTED"
            )
            memory.removed_step = result.step_no
            memory.removed_at = result.virtual_time

    @staticmethod
    def _schedule(session, run_id, result, record) -> None:
        existing = session.scalar(
            select(RunScheduleRevision).where(
                RunScheduleRevision.run_id == run_id,
                RunScheduleRevision.agent_key == record.agent_key,
                RunScheduleRevision.content_hash == record.content_hash,
            )
        )
        if existing is not None:
            return
        revision_no = (
            session.scalar(
                select(func.max(RunScheduleRevision.revision_no)).where(
                    RunScheduleRevision.run_id == run_id,
                    RunScheduleRevision.agent_key == record.agent_key,
                )
            )
            or 0
        ) + 1
        value = RunScheduleRevision(
            id=str(record.revision_id),
            run_id=run_id,
            agent_key=record.agent_key,
            revision_no=revision_no,
            effective_step=result.step_no,
            effective_at=result.virtual_time,
            reason=record.reason,
            source_event_id=(str(record.source_event_id) if record.source_event_id else None),
            content_hash=record.content_hash,
            items_json=[dict(item) for item in record.schedule],
        )
        session.add(value)
        agent_summary = session.get(RunAgentSummary, (run_id, record.agent_key))
        if agent_summary is not None:
            agent_summary.latest_schedule_revision_id = value.id

    @staticmethod
    def _default_capabilities() -> dict:
        return {
            key: {"state": "AVAILABLE", "reason": None}
            for key in (
                "summary",
                "timeline",
                "agents",
                "conversations",
                "memories",
                "operations",
            )
        }
