"""Translate actual legacy-domain outcomes into a complete StepResult ledger."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
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
    StepEffectKind,
    StepEffectRecord,
    StepResult,
    StepResultBuilder,
    deterministic_record_id,
)


class StepResultCollector:
    """Single-step adapter; facts are captured where the domain produced them."""

    def __init__(self, builder: StepResultBuilder, *, name_to_key: Mapping[str, str]):
        self.builder = builder
        self._name_to_key = dict(name_to_key)
        self._sequences = {
            "conversation": 0,
            "memory": 0,
            "schedule": 0,
            "domain": 0,
            "effect": 0,
        }

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
        self._add_effect(
            StepEffectKind.ACTION_SELECTED,
            (agent_key,),
            {
                "action": self._event_payload(event),
                "object_event": (
                    self._event_payload(agent.get_event(False))
                    if agent.get_event(False) is not None
                    else None
                ),
                "from_coord": list(from_coord),
                "to_coord": list(to_coord),
                "location": list(agent.get_tile().get_address()),
            },
            key=f"action:{agent_key}",
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
    def _event_payload(event) -> dict | None:
        if event is None:
            return None
        serializer = getattr(event, "to_dict", None)
        if callable(serializer):
            return dict(serializer())
        return {
            "subject": getattr(event, "subject", None),
            "predicate": getattr(event, "predicate", None),
            "object": getattr(event, "object", None),
            "describe": event.get_describe(),
            "address": list(getattr(event, "address", ()) or ()),
            "emoji": getattr(event, "emoji", None),
        }

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
            "external_observations": list(info.get("external_observations") or ()),
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
            semantic_event = event.get("event") or {}
            created_at = event.get("created_at")
            expires_at = event.get("expires_at")
            memory_event_id = deterministic_record_id(
                self.builder.run_id, self.builder.step_no, "memory", str(sequence)
            )
            self.builder.add_memory_delta(
                MemoryDelta(
                    event_id=memory_event_id,
                    sequence=sequence,
                    agent_key=event["agent_key"],
                    memory_id=event["memory_id"],
                    kind=delta_kind,
                    memory_type=event["memory_type"],
                    description=event.get("description"),
                    poignancy=event.get("poignancy"),
                    source_event_id=(
                        UUID(event["source_event_id"])
                        if event.get("source_event_id")
                        else None
                    ),
                    subject=semantic_event.get("subject"),
                    predicate=semantic_event.get("predicate"),
                    object=semantic_event.get("object"),
                    address=tuple(semantic_event.get("address") or event.get("address") or ()),
                    created_at=(datetime.fromisoformat(created_at) if created_at else None),
                    expires_at=(datetime.fromisoformat(expires_at) if expires_at else None),
                    evidence_memory_ids=tuple(event.get("evidence_memory_ids") or ()),
                )
            )
            if delta_kind == MemoryDeltaKind.CREATED and semantic_event:
                self._add_effect(
                    StepEffectKind.EVENT_PERCEIVED,
                    (event["agent_key"],),
                    {
                        "memory_id": event["memory_id"],
                        "memory_type": event["memory_type"],
                        "event": dict(semantic_event),
                    },
                    key=f"perceived:{event['agent_key']}:{event['memory_id']}",
                    source_effect_id=memory_event_id,
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
        elif kind == "game_object_interaction":
            location = event.get("location") or ()
            if isinstance(location, (list, tuple)):
                location = ":".join(str(segment) for segment in location)
            common = {
                "object_key": event["object_key"],
                "object_name": event["object_name"],
                "interaction_key": event["interaction_key"],
                "skill_name": event["skill_name"],
                "skill_revision": event.get("skill_revision"),
                "location": str(location),
                "source_type": "GAME_OBJECT_SKILL",
                "source_id": event["object_key"],
            }
            self._add_domain_event(
                "GAME_OBJECT_INTERACTION_REQUESTED",
                (event["agent_key"],),
                {
                    **common,
                    "title": f"{event['object_name']}收到交互请求",
                    "detail": event["request"],
                    "request": event["request"],
                },
            )
            self._add_domain_event(
                "GAME_OBJECT_SKILL_RESPONDED",
                (event["agent_key"],),
                {
                    **common,
                    "title": f"{event['object_name']}返回外部信息",
                    "detail": event["response"],
                    "request": event["request"],
                    "response": event["response"],
                    "agent_decision": event["agent_decision"],
                    "skill_trace": list(event.get("trace") or ()),
                },
            )
            self._add_effect(
                StepEffectKind.SKILL_EXECUTED,
                (event["agent_key"],),
                {
                    "input_text": event["request"],
                    "output_text": event["response"],
                    "trace": list(event.get("trace") or ()),
                    "source_type": "GAME_OBJECT_SKILL",
                    "source_id": event["object_key"],
                },
                key=(
                    f"skill:{event['agent_key']}:{event['object_key']}:"
                    f"{event['interaction_key']}"
                ),
                skill_name=event["skill_name"],
                skill_revision=event.get("skill_revision"),
            )

        elif kind == "skill_execution":
            self._add_effect(
                StepEffectKind.SKILL_EXECUTED,
                (event["agent_key"],),
                {
                    "output_text": event.get("output_text"),
                    "execution_source": event.get("execution_source"),
                    "source_type": "AGENT_COGNITION",
                },
                key=(
                    f"skill:{event['agent_key']}:{event['skill_name']}:"
                    f"{self._sequences['effect'] + 1}"
                ),
                skill_name=event["skill_name"],
                skill_revision=event.get("skill_revision"),
            )

    def capture_event(self, event: Mapping) -> None:
        """Capture a side effect emitted by a run-scoped Skill service."""

        self._capture_event(event)

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

    def _add_effect(
        self,
        kind: StepEffectKind,
        agent_keys,
        payload,
        *,
        key: str,
        source_effect_id: UUID | None = None,
        skill_name: str | None = None,
        skill_revision: str | None = None,
    ) -> None:
        self._sequences["effect"] += 1
        self.builder.add_effect(
            StepEffectRecord(
                effect_id=deterministic_record_id(
                    self.builder.run_id,
                    self.builder.step_no,
                    "effect",
                    key,
                ),
                sequence=self._sequences["effect"],
                kind=kind,
                agent_keys=tuple(sorted(agent_keys)),
                payload=payload,
                source_effect_id=source_effect_id,
                skill_name=skill_name,
                skill_revision=skill_revision,
            )
        )

    def freeze(self) -> StepResult:
        return self.builder.freeze()
