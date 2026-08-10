"""Translate actual legacy-domain outcomes into a complete StepResult ledger."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from .results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    ConversationMessage,
    ConversationRecord,
    DomainEventRecord,
    MemoryDelta,
    MemoryDeltaKind,
    ScheduleRevisionRecord,
    StepResult,
    StepResultBuilder,
    deterministic_record_id,
)


class StepResultCollector:
    """Single-step adapter; facts are captured where the domain produced them."""

    def __init__(self, builder: StepResultBuilder, *, name_to_key: Mapping[str, str]):
        self.builder = builder
        self._name_to_key = dict(name_to_key)
        self._sequences = {"conversation": 0, "memory": 0, "schedule": 0, "domain": 0}

    def capture_agent(
        self,
        agent_key: str,
        agent,
        from_coord,
        outcome: Mapping,
        *,
        executed_path=None,
        planned_path=None,
        remaining_path=None,
    ) -> None:
        plan = outcome.get("plan") or {}
        info = outcome.get("info") or {}
        planned_path = tuple(
            tuple(coord)
            for coord in (
                plan.get("path") if planned_path is None else planned_path
            )
            or ()
        )
        observed_path = tuple(
            tuple(coord)
            for coord in (
                planned_path if executed_path is None else executed_path
            )
            or ()
        )
        remaining_path = tuple(
            tuple(coord) for coord in (remaining_path or ())
        )
        to_coord = tuple(agent.coord)
        event = agent.get_event()
        description = event.get_describe() if event else ""
        predicate = event.predicate if event else ""
        if observed_path and tuple(from_coord) != to_coord:
            activity = ActivityKind.MOVING
        elif predicate in {"对话", "chat", "聊天"}:
            activity = ActivityKind.CHAT
        elif any(word in description.casefold() for word in ("sleep", "rest", "睡", "休息")):
            activity = ActivityKind.REST
        else:
            activity = ActivityKind.OTHER
        self.builder.add_agent(
            AgentStepResult(
                agent_key=agent_key,
                from_coord=tuple(from_coord),
                to_coord=to_coord,
                path=observed_path,
                action=ActionSnapshot(
                    description=description,
                    emoji=getattr(event, "emoji", None),
                    object_description=(
                        agent.get_event(False).get_describe()
                        if agent.get_event(False) is not None
                        else None
                    ),
                ),
                activity_kind=activity,
                location=tuple(agent.get_tile().get_address()),
                currently=info.get("currently"),
                path_source="OBSERVED",
                decision_context=self._decision_context(
                    plan,
                    info,
                    planned_path=planned_path,
                    remaining_path=remaining_path,
                ),
            )
        )
        if tuple(from_coord) != to_coord:
            self._add_domain_event(
                "MOVED",
                (agent_key,),
                {"from_coord": tuple(from_coord), "to_coord": to_coord, "path": observed_path},
            )
        for raw_event in outcome.get("events") or ():
            self._capture_event(raw_event)

    @staticmethod
    def _decision_context(
        plan: Mapping,
        info: Mapping,
        *,
        planned_path=None,
        remaining_path=(),
    ) -> dict:
        """Keep the human-readable decision facts without duplicating full memory storage."""

        if planned_path is None:
            planned_path = tuple(
                tuple(coord) for coord in (plan.get("path") or ())
            )

        perceptions = []
        for node_id, abstract in list((info.get("concepts") or {}).items())[:20]:
            perceptions.append({"node_id": str(node_id), "content": abstract})
        schedule = info.get("schedule") or {}
        action = info.get("action") or {}
        associate = info.get("associate") or {}
        return {
            "perceptions": perceptions,
            "schedule": schedule,
            "action": action,
            # path remains a backwards-compatible alias for the newly planned
            # route; StepResult.path is exclusively the route already executed
            # during this committed interval.
            "path": [list(coord) for coord in planned_path],
            "planned_path": [list(coord) for coord in planned_path],
            "remaining_path": [list(coord) for coord in remaining_path],
            "memory_counts": {
                kind: len(associate.get(kind) or ())
                for kind in ("event", "chat", "thought")
            },
        }

    def _capture_event(self, event: Mapping) -> None:
        kind = event.get("kind")
        if kind == "conversation":
            self._sequences["conversation"] += 1
            sequence = self._sequences["conversation"]
            conversation_id = deterministic_record_id(
                self.builder.run_id,
                self.builder.step_no,
                "conversation",
                str(sequence),
            )
            messages = []
            for message_sequence, (speaker, content) in enumerate(event["messages"], 1):
                speaker_key = self._name_to_key.get(speaker, speaker)
                messages.append(
                    ConversationMessage(
                        message_id=deterministic_record_id(
                            self.builder.run_id,
                            self.builder.step_no,
                            "message",
                            f"{sequence}:{message_sequence}",
                        ),
                        sequence=message_sequence,
                        speaker_agent_key=speaker_key,
                        content=content,
                    )
                )
            record = ConversationRecord(
                conversation_id=conversation_id,
                participant_agent_keys=tuple(sorted(event["participants"])),
                location=tuple(event["location"]),
                messages=tuple(messages),
                summary=event.get("summary"),
                ended_reason=event.get("ended_reason"),
                duration_minutes=event.get("duration_minutes"),
                duration_source=event.get("duration_source", "ESTIMATED"),
            )
            self.builder.add_conversation(record)
            self._add_domain_event(
                "CONVERSATION",
                record.participant_agent_keys,
                {"conversation_id": str(conversation_id), "message_count": len(messages)},
            )
        elif kind == "memory":
            self._sequences["memory"] += 1
            sequence = self._sequences["memory"]
            try:
                delta_kind = MemoryDeltaKind(event.get("memory_kind", "CREATED"))
            except ValueError as exc:
                raise ValueError(
                    f"unsupported memory delta kind: {event.get('memory_kind')!r}"
                ) from exc
            self.builder.add_memory_delta(
                MemoryDelta(
                    event_id=deterministic_record_id(
                        self.builder.run_id, self.builder.step_no, "memory", str(sequence)
                    ),
                    sequence=sequence,
                    agent_key=event["agent_key"],
                    memory_id=event["memory_id"],
                    kind=delta_kind,
                    memory_type=event["memory_type"],
                    description=event.get("description"),
                    poignancy=event.get("poignancy"),
                )
            )
        elif kind == "schedule":
            self._sequences["schedule"] += 1
            sequence = self._sequences["schedule"]
            self.builder.add_schedule_revision(
                ScheduleRevisionRecord(
                    revision_id=deterministic_record_id(
                        self.builder.run_id, self.builder.step_no, "schedule", str(sequence)
                    ),
                    sequence=sequence,
                    agent_key=event["agent_key"],
                    reason=event["reason"],
                    source_event_id=None,
                    content_hash=event["content_hash"],
                    schedule=tuple(event["schedule"]),
                )
            )

    def _add_domain_event(self, event_type: str, agent_keys, payload) -> None:
        self._sequences["domain"] += 1
        sequence = self._sequences["domain"]
        self.builder.add_domain_event(
            DomainEventRecord(
                event_id=deterministic_record_id(
                    self.builder.run_id, self.builder.step_no, "domain", str(sequence)
                ),
                sequence=sequence,
                event_type=event_type,
                agent_keys=tuple(sorted(agent_keys)),
                payload=payload,
            )
        )

    def freeze(self) -> StepResult:
        return self.builder.freeze()
