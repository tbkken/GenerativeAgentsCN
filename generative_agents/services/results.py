"""Run-scoped query service for the six experiment result views."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ArtifactJob,
    ExperimentRevision,
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunArtifact,
    RunAttempt,
    RunConversation,
    RunConversationParticipant,
    RunDomainEvent,
    RunMemoryEvent,
    RunMessage,
    RunModelUsage,
    RunRelationshipEdge,
    RunResultSummary,
    RunScheduleRevision,
    RunStep,
)

from .errors import ServiceError, not_found


class ResultQueryService:
    def __init__(self, database: Database):
        self._database = database

    def summary(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            value = session.get(RunResultSummary, run_id)
            if value is None:
                return {
                    "run_id": run_id,
                    "run_status": run.status,
                    "result_state": "EMPTY",
                    "available_step": 0,
                    "requested_steps": run.requested_steps,
                    "result_version": 0,
                    "capabilities": self._empty_capabilities(),
                    "counts": self._zero_counts(),
                }
            edges = list(
                session.scalars(
                    select(RunRelationshipEdge)
                    .where(RunRelationshipEdge.run_id == run_id)
                    .order_by(
                        RunRelationshipEdge.conversation_count.desc(),
                        RunRelationshipEdge.agent_a,
                        RunRelationshipEdge.agent_b,
                    )
                    .limit(20)
                )
            )
            events = list(
                session.scalars(
                    select(RunDomainEvent)
                    .where(RunDomainEvent.run_id == run_id)
                    .order_by(
                        RunDomainEvent.importance_score.desc(),
                        RunDomainEvent.virtual_time.desc(),
                    )
                    .limit(10)
                )
            )
            return {
                "run_id": run_id,
                "run_status": run.status,
                "result_state": value.result_state,
                "available_step": value.available_step,
                "requested_steps": run.requested_steps,
                "virtual_time": value.virtual_time.isoformat() if value.virtual_time else None,
                "result_version": value.result_version,
                "projection_version": value.projection_version,
                "capabilities": value.capabilities_json,
                "counts": {
                    "actions": value.action_count,
                    "conversations": value.conversation_count,
                    "messages": value.message_count,
                    "memories": value.memory_count,
                    "model_calls": value.model_call_count,
                    "model_retries": value.model_retry_count,
                },
                "conversation_network": {
                    "weight_metric": "conversation_count",
                    "edges": [
                        {
                            "agent_a": edge.agent_a,
                            "agent_a_name": names.get(edge.agent_a, edge.agent_a),
                            "agent_b": edge.agent_b,
                            "agent_b_name": names.get(edge.agent_b, edge.agent_b),
                            "conversation_count": edge.conversation_count,
                            "message_count": edge.message_count,
                            "duration_minutes": edge.duration_minutes,
                        }
                        for edge in edges
                    ],
                },
                "key_events": [self._event(event, names) for event in events],
            }

    def timeline(
        self,
        run_id: str,
        *,
        from_step: int = 1,
        to_step: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        self._validate_limit(limit, maximum=500)
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            available = self._available_step(session, run_id)
            upper = available if to_step is None else min(to_step, available)
            if from_step < 1 or upper < from_step:
                return {
                    "run_id": run_id,
                    "available_step": available,
                    "requested_steps": run.requested_steps,
                    "steps": [],
                    "events": [],
                    "agent_steps": [],
                }
            steps = list(
                session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.run_id == run_id,
                        RunStep.step_no >= from_step,
                        RunStep.step_no <= upper,
                    )
                    .order_by(RunStep.step_no)
                    .limit(limit)
                )
            )
            events = list(
                session.scalars(
                    select(RunDomainEvent)
                    .where(
                        RunDomainEvent.run_id == run_id,
                        RunDomainEvent.step_no >= from_step,
                        RunDomainEvent.step_no <= upper,
                    )
                    .order_by(RunDomainEvent.step_no, RunDomainEvent.id)
                    .limit(limit * 5)
                )
            )
            agent_steps = list(
                session.scalars(
                    select(RunAgentStep)
                    .where(
                        RunAgentStep.run_id == run_id,
                        RunAgentStep.step_no >= from_step,
                        RunAgentStep.step_no <= upper,
                    )
                    .order_by(RunAgentStep.step_no, RunAgentStep.agent_key)
                    .limit(limit * 500)
                )
            )
            return {
                "run_id": run_id,
                "available_step": available,
                "requested_steps": run.requested_steps,
                "steps": [self._step(step) for step in steps],
                "events": [self._event(event, names) for event in events],
                "agent_steps": [
                    {
                        "step_no": row.step_no,
                        "agent_key": row.agent_key,
                        "agent_name": names.get(row.agent_key, row.agent_key),
                        "coord": [row.x, row.y],
                        "address": row.address,
                        "action": row.action_text,
                        "emoji": row.action_emoji,
                        "activity_kind": row.activity_kind,
                        "sample_kind": row.path_source,
                    }
                    for row in agent_steps
                ],
            }

    def agents(self, run_id: str, *, limit: int = 100) -> dict[str, Any]:
        self._validate_limit(limit, maximum=500)
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            rows = list(
                session.scalars(
                    select(RunAgentSummary)
                    .where(RunAgentSummary.run_id == run_id)
                    .order_by(
                        RunAgentSummary.conversation_count.desc(),
                        RunAgentSummary.agent_key,
                    )
                    .limit(limit)
                )
            )
            return {
                "run_id": run_id,
                "items": [self._agent(row, names) for row in rows],
            }

    def agent(self, run_id: str, agent_key: str, *, step_limit: int = 200) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            summary = session.get(RunAgentSummary, (run_id, agent_key))
            if summary is None:
                raise not_found("agent_result", agent_key)
            steps = list(
                session.scalars(
                    select(RunAgentStep)
                    .where(
                        RunAgentStep.run_id == run_id,
                        RunAgentStep.agent_key == agent_key,
                    )
                    .order_by(RunAgentStep.step_no.desc())
                    .limit(step_limit)
                )
            )
            schedule = session.scalar(
                select(RunScheduleRevision)
                .where(
                    RunScheduleRevision.run_id == run_id,
                    RunScheduleRevision.agent_key == agent_key,
                )
                .order_by(RunScheduleRevision.revision_no.desc())
                .limit(1)
            )
            return {
                "run_id": run_id,
                "agent": self._agent(summary, names),
                "steps": [
                    {
                        "step_no": row.step_no,
                        "virtual_time": row.virtual_time.isoformat(),
                        "coord": [row.x, row.y],
                        "address": row.address,
                        "action": row.action_text,
                        "emoji": row.action_emoji,
                        "activity_kind": row.activity_kind,
                        "sample_kind": row.path_source,
                    }
                    for row in reversed(steps)
                ],
                "latest_schedule": (
                    {
                        "revision_no": schedule.revision_no,
                        "effective_step": schedule.effective_step,
                        "reason": schedule.reason,
                        "items": schedule.items_json,
                    }
                    if schedule
                    else None
                ),
            }

    def conversations(
        self,
        run_id: str,
        *,
        agent_key: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._validate_limit(limit, maximum=100)
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            statement = select(RunConversation).where(RunConversation.run_id == run_id)
            if agent_key:
                statement = statement.join(
                    RunConversationParticipant,
                    RunConversationParticipant.conversation_id == RunConversation.id,
                ).where(
                    RunConversationParticipant.run_id == run_id,
                    RunConversationParticipant.agent_key == agent_key,
                )
            if query:
                matched_ids = select(RunMessage.conversation_id).where(
                    RunMessage.run_id == run_id,
                    RunMessage.content.contains(query),
                )
                statement = statement.where(RunConversation.id.in_(matched_ids))
            rows = list(
                session.scalars(
                    statement.order_by(
                        RunConversation.started_at.desc(), RunConversation.id.desc()
                    )
                    .offset(offset)
                    .limit(limit + 1)
                ).unique()
            )
            has_more = len(rows) > limit
            return {
                "run_id": run_id,
                "items": [self._conversation(row, names) for row in rows[:limit]],
                "next_offset": offset + limit if has_more else None,
            }

    def conversation(self, run_id: str, conversation_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            conversation = session.get(RunConversation, conversation_id)
            if conversation is None or conversation.run_id != run_id:
                raise not_found("conversation", conversation_id)
            messages = list(
                session.scalars(
                    select(RunMessage)
                    .where(
                        RunMessage.run_id == run_id,
                        RunMessage.conversation_id == conversation_id,
                    )
                    .order_by(RunMessage.sequence_no)
                )
            )
            return {
                **self._conversation(conversation, names),
                "messages": [
                    {
                        "message_id": message.id,
                        "sequence": message.sequence_no,
                        "speaker_agent_key": message.speaker_agent_key,
                        "speaker_name": names.get(
                            message.speaker_agent_key, message.speaker_agent_key
                        ),
                        "content": message.content,
                        "observed_at": message.observed_at.isoformat(),
                    }
                    for message in messages
                ],
            }

    def memories(
        self,
        run_id: str,
        *,
        agent_key: str | None = None,
        memory_type: str | None = None,
        state: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._validate_limit(limit, maximum=100)
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            names = self._agent_names(session, run)
            statement = select(RunMemoryEvent).where(RunMemoryEvent.run_id == run_id)
            if agent_key:
                statement = statement.where(RunMemoryEvent.agent_key == agent_key)
            if memory_type:
                statement = statement.where(RunMemoryEvent.memory_type == memory_type)
            if state:
                statement = statement.where(RunMemoryEvent.state == state)
            if query:
                statement = statement.where(RunMemoryEvent.description.contains(query))
            rows = list(
                session.scalars(
                    statement.order_by(
                        RunMemoryEvent.created_step.desc(),
                        RunMemoryEvent.agent_key,
                        RunMemoryEvent.memory_node_id,
                    )
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
            has_more = len(rows) > limit
            return {
                "run_id": run_id,
                "items": [
                    {
                        "memory_id": row.memory_node_id,
                        "agent_key": row.agent_key,
                        "agent_name": names.get(row.agent_key, row.agent_key),
                        "type": row.memory_type,
                        "origin": row.origin,
                        "state": row.state,
                        "description": row.description,
                        "poignancy": row.poignancy,
                        "created_step": row.created_step,
                        "last_accessed_step": row.last_accessed_step,
                        "removed_step": row.removed_step,
                    }
                    for row in rows[:limit]
                ],
                "next_offset": offset + limit if has_more else None,
            }

    def operations(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            attempts = list(
                session.scalars(
                    select(RunAttempt)
                    .where(RunAttempt.run_id == run_id)
                    .order_by(RunAttempt.attempt_no)
                )
            )
            usage = list(
                session.scalars(
                    select(RunModelUsage)
                    .where(RunModelUsage.run_id == run_id)
                    .order_by(RunModelUsage.logical_call_count.desc())
                )
            )
            artifacts = list(
                session.scalars(
                    select(RunArtifact)
                    .where(RunArtifact.run_id == run_id)
                    .order_by(RunArtifact.created_at.desc())
                )
            )
            jobs = list(
                session.scalars(
                    select(ArtifactJob)
                    .where(ArtifactJob.run_id == run_id)
                    .order_by(ArtifactJob.created_at.desc())
                )
            )
            return {
                "run_id": run_id,
                "run_status": run.status,
                "attempts": [
                    {
                        "attempt_id": item.id,
                        "attempt_no": item.attempt_no,
                        "status": item.status,
                        "start_step": item.start_step,
                        "end_step": item.end_step,
                        "stop_reason": item.stop_reason,
                        "started_at": item.started_at.isoformat(),
                        "ended_at": item.ended_at.isoformat() if item.ended_at else None,
                    }
                    for item in attempts
                ],
                "model_usage": [
                    {
                        "purpose": item.purpose,
                        "provider": item.provider,
                        "model": item.resolved_model,
                        "logical_calls": item.logical_call_count,
                        "physical_attempts": item.physical_attempt_count,
                        "retries": item.retry_count,
                        "input_tokens": item.input_tokens,
                        "output_tokens": item.output_tokens,
                        "max_latency_ms": item.max_latency_ms,
                    }
                    for item in usage
                ],
                "artifacts": [
                    {
                        "artifact_id": item.id,
                        "type": item.artifact_type,
                        "logical_name": item.logical_name,
                        "media_type": item.media_type,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                        "state": item.state,
                    }
                    for item in artifacts
                ],
                "artifact_jobs": [
                    {
                        "job_id": item.id,
                        "type": item.job_type,
                        "status": item.status,
                        "progress": item.progress,
                        "artifact_id": item.artifact_id,
                        "error_summary": item.error_summary,
                    }
                    for item in jobs
                ],
            }

    @staticmethod
    def _run(session, run_id: str) -> Run:
        value = session.get(Run, run_id)
        if value is None:
            raise not_found("run", run_id)
        return value

    @staticmethod
    def _available_step(session, run_id: str) -> int:
        value = session.get(RunResultSummary, run_id)
        return value.available_step if value else 0

    @staticmethod
    def _agent_names(session, run: Run) -> dict[str, str]:
        revision = session.get(ExperimentRevision, run.revision_id)
        if revision is None:
            return {}
        return {
            item["agent_key"]: item.get("name") or item["agent_key"]
            for item in (revision.definition_json.get("agents") or [])
            if item.get("agent_key")
        }

    @staticmethod
    def _validate_limit(limit: int, *, maximum: int) -> None:
        if limit < 1 or limit > maximum:
            raise ServiceError(
                "INVALID_LIMIT", f"limit 必须在 1 到 {maximum} 之间", status_code=422
            )

    @staticmethod
    def _step(row: RunStep) -> dict:
        return {
            "step_no": row.step_no,
            "virtual_time": row.virtual_time.isoformat(),
            "actions": row.action_count,
            "movements": row.movement_count,
            "conversations": row.conversation_count,
            "messages": row.message_count,
            "memories_created": row.memory_created_count,
            "model_calls": row.model_logical_calls,
            "checkpoint": row.checkpoint,
            "sample_kind": "OBSERVED",
        }

    @staticmethod
    def _event(row: RunDomainEvent, names: dict[str, str] | None = None) -> dict:
        names = names or {}
        return {
            "event_id": row.id,
            "step_no": row.step_no,
            "virtual_time": row.virtual_time.isoformat(),
            "event_type": row.event_type,
            "primary_agent_key": row.primary_agent_key,
            "primary_agent_name": names.get(
                row.primary_agent_key, row.primary_agent_key
            ),
            "title": row.title,
            "detail": row.detail,
            "location": row.location,
            "importance_score": row.importance_score,
            "source_type": row.source_type,
            "source_id": row.source_id,
        }

    @staticmethod
    def _agent(row: RunAgentSummary, names: dict[str, str] | None = None) -> dict:
        names = names or {}
        return {
            "agent_key": row.agent_key,
            "display_name": names.get(row.agent_key, row.agent_key),
            "coord": [row.x, row.y],
            "address": row.address,
            "currently": row.currently_text,
            "action_count": row.action_count,
            "movement_steps": row.movement_steps,
            "conversation_count": row.conversation_count,
            "message_count": row.message_count,
            "memory_created_count": row.memory_created_count,
            "activity_minutes": {
                "REST": row.rest_minutes,
                "CHAT": row.chat_minutes,
                "MOVING": row.moving_minutes,
                "OTHER": row.other_minutes,
            },
            "updated_step": row.updated_step,
        }

    @staticmethod
    def _conversation(
        row: RunConversation, names: dict[str, str] | None = None
    ) -> dict:
        names = names or {}
        return {
            "conversation_id": row.id,
            "start_step": row.start_step,
            "started_at": row.started_at.isoformat(),
            "duration_minutes": row.duration_minutes,
            "duration_source": row.duration_source,
            "location": row.location,
            "participants": [row.initiator_agent_key, row.responder_agent_key],
            "participant_names": [
                names.get(row.initiator_agent_key, row.initiator_agent_key),
                names.get(row.responder_agent_key, row.responder_agent_key),
            ],
            "message_count": row.message_count,
            "summary": row.summary,
            "ended_reason": row.ended_reason,
        }

    @staticmethod
    def _empty_capabilities() -> dict:
        return {
            key: {"state": "UNAVAILABLE", "reason": "NO_COMMITTED_STEPS"}
            for key in (
                "summary",
                "timeline",
                "agents",
                "conversations",
                "memories",
                "operations",
            )
        }

    @staticmethod
    def _zero_counts() -> dict:
        return {
            "actions": 0,
            "conversations": 0,
            "messages": 0,
            "memories": 0,
            "model_calls": 0,
            "model_retries": 0,
        }
