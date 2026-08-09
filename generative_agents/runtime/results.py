"""Immutable, complete result envelope for one committed simulation step."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5


class ActivityKind(StrEnum):
    REST = "REST"
    CHAT = "CHAT"
    MOVING = "MOVING"
    OTHER = "OTHER"


class MemoryDeltaKind(StrEnum):
    CREATED = "CREATED"
    ACCESSED = "ACCESSED"
    EXPIRED = "EXPIRED"
    EVICTED = "EVICTED"


@dataclass(frozen=True, slots=True)
class ActionSnapshot:
    description: str
    emoji: str | None = None
    object_description: str | None = None


@dataclass(frozen=True, slots=True)
class AgentStepResult:
    agent_key: str
    from_coord: tuple[int, int]
    to_coord: tuple[int, int]
    path: tuple[tuple[int, int], ...]
    action: ActionSnapshot
    activity_kind: ActivityKind
    location: tuple[str, ...]
    currently: str | None = None
    schedule_item_id: str | None = None
    path_source: str = "OBSERVED"
    decision_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: UUID
    sequence: int
    speaker_agent_key: str
    content: str


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: UUID
    participant_agent_keys: tuple[str, str]
    location: tuple[str, ...]
    messages: tuple[ConversationMessage, ...]
    summary: str | None = None
    ended_reason: str | None = None
    duration_minutes: int | None = None
    duration_source: str = "ESTIMATED"


@dataclass(frozen=True, slots=True)
class MemoryDelta:
    event_id: UUID
    sequence: int
    agent_key: str
    memory_id: str
    kind: MemoryDeltaKind
    memory_type: str
    description: str | None = None
    poignancy: float | None = None
    source_event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ScheduleRevisionRecord:
    revision_id: UUID
    sequence: int
    agent_key: str
    reason: str
    source_event_id: UUID | None
    content_hash: str
    schedule: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DomainEventRecord:
    event_id: UUID
    sequence: int
    event_type: str
    agent_keys: tuple[str, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelUsageDelta:
    logical_call_id: UUID
    purpose: str
    provider: str
    model: str
    physical_attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    fallback_used: bool = False


def deterministic_record_id(run_id: UUID, step_no: int, kind: str, key: str) -> UUID:
    """Create a replay-stable ID scoped by run and step."""

    if step_no < 1:
        raise ValueError("step_no must be greater than zero")
    return uuid5(run_id, f"{step_no}:{kind}:{key}")


def _wire_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _wire_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class StepResult:
    run_id: UUID
    attempt_id: UUID
    step_no: int
    virtual_time: datetime
    agents: tuple[AgentStepResult, ...]
    conversations: tuple[ConversationRecord, ...]
    memory_deltas: tuple[MemoryDelta, ...]
    schedule_revisions: tuple[ScheduleRevisionRecord, ...]
    domain_events: tuple[DomainEventRecord, ...]
    committed_model_usage: tuple[ModelUsageDelta, ...]

    def __post_init__(self) -> None:
        if self.step_no < 1:
            raise ValueError("step_no must be greater than zero")
        if self.virtual_time.tzinfo is None:
            raise ValueError("virtual_time must be timezone-aware")
        agent_keys = [item.agent_key for item in self.agents]
        if len(agent_keys) != len(set(agent_keys)):
            raise ValueError("agents must contain at most one result per agent_key")

    def to_dict(self) -> dict[str, Any]:
        return _wire_value(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StepResult":
        """Rehydrate a verified frame for deterministic projection rebuilds."""

        def optional_uuid(item):
            return UUID(item) if item else None

        return cls(
            run_id=UUID(value["run_id"]),
            attempt_id=UUID(value["attempt_id"]),
            step_no=int(value["step_no"]),
            virtual_time=datetime.fromisoformat(value["virtual_time"]),
            agents=tuple(
                AgentStepResult(
                    agent_key=item["agent_key"],
                    from_coord=tuple(item["from_coord"]),
                    to_coord=tuple(item["to_coord"]),
                    path=tuple(tuple(coord) for coord in item["path"]),
                    action=ActionSnapshot(**item["action"]),
                    activity_kind=ActivityKind(item["activity_kind"]),
                    location=tuple(item["location"]),
                    currently=item.get("currently"),
                    schedule_item_id=item.get("schedule_item_id"),
                    path_source=item.get("path_source", "OBSERVED"),
                    decision_context=item.get("decision_context") or {},
                )
                for item in value.get("agents", ())
            ),
            conversations=tuple(
                ConversationRecord(
                    conversation_id=UUID(item["conversation_id"]),
                    participant_agent_keys=tuple(item["participant_agent_keys"]),
                    location=tuple(item["location"]),
                    messages=tuple(
                        ConversationMessage(
                            message_id=UUID(message["message_id"]),
                            sequence=int(message["sequence"]),
                            speaker_agent_key=message["speaker_agent_key"],
                            content=message["content"],
                        )
                        for message in item.get("messages", ())
                    ),
                    summary=item.get("summary"),
                    ended_reason=item.get("ended_reason"),
                    duration_minutes=item.get("duration_minutes"),
                    duration_source=item.get("duration_source", "ESTIMATED"),
                )
                for item in value.get("conversations", ())
            ),
            memory_deltas=tuple(
                MemoryDelta(
                    event_id=UUID(item["event_id"]),
                    sequence=int(item["sequence"]),
                    agent_key=item["agent_key"],
                    memory_id=item["memory_id"],
                    kind=MemoryDeltaKind(item["kind"]),
                    memory_type=item["memory_type"],
                    description=item.get("description"),
                    poignancy=item.get("poignancy"),
                    source_event_id=optional_uuid(item.get("source_event_id")),
                )
                for item in value.get("memory_deltas", ())
            ),
            schedule_revisions=tuple(
                ScheduleRevisionRecord(
                    revision_id=UUID(item["revision_id"]),
                    sequence=int(item["sequence"]),
                    agent_key=item["agent_key"],
                    reason=item["reason"],
                    source_event_id=optional_uuid(item.get("source_event_id")),
                    content_hash=item["content_hash"],
                    schedule=tuple(item.get("schedule", ())),
                )
                for item in value.get("schedule_revisions", ())
            ),
            domain_events=tuple(
                DomainEventRecord(
                    event_id=UUID(item["event_id"]),
                    sequence=int(item["sequence"]),
                    event_type=item["event_type"],
                    agent_keys=tuple(item.get("agent_keys", ())),
                    payload=dict(item.get("payload", {})),
                )
                for item in value.get("domain_events", ())
            ),
            committed_model_usage=tuple(
                ModelUsageDelta(
                    logical_call_id=UUID(item["logical_call_id"]),
                    purpose=item["purpose"],
                    provider=item["provider"],
                    model=item["model"],
                    physical_attempts=int(item["physical_attempts"]),
                    prompt_tokens=item.get("prompt_tokens"),
                    completion_tokens=item.get("completion_tokens"),
                    total_tokens=item.get("total_tokens"),
                    latency_ms=item.get("latency_ms"),
                    fallback_used=bool(item.get("fallback_used", False)),
                )
                for item in value.get("committed_model_usage", ())
            ),
        )


@dataclass(slots=True)
class StepResultBuilder:
    """The only mutable collector inside a simulation step."""

    run_id: UUID
    attempt_id: UUID
    step_no: int
    virtual_time: datetime
    _agents: list[AgentStepResult] = field(default_factory=list)
    _conversations: list[ConversationRecord] = field(default_factory=list)
    _memory_deltas: list[MemoryDelta] = field(default_factory=list)
    _schedule_revisions: list[ScheduleRevisionRecord] = field(default_factory=list)
    _domain_events: list[DomainEventRecord] = field(default_factory=list)
    _model_usage: list[ModelUsageDelta] = field(default_factory=list)
    _frozen: bool = False

    def _append(self, target: list[Any], value: Any) -> None:
        if self._frozen:
            raise RuntimeError("StepResultBuilder is already frozen")
        target.append(value)

    def add_agent(self, value: AgentStepResult) -> None:
        self._append(self._agents, value)

    def add_conversation(self, value: ConversationRecord) -> None:
        self._append(self._conversations, value)

    def add_memory_delta(self, value: MemoryDelta) -> None:
        self._append(self._memory_deltas, value)

    def add_schedule_revision(self, value: ScheduleRevisionRecord) -> None:
        self._append(self._schedule_revisions, value)

    def add_domain_event(self, value: DomainEventRecord) -> None:
        self._append(self._domain_events, value)

    def add_model_usage(self, value: ModelUsageDelta) -> None:
        self._append(self._model_usage, value)

    def extend_model_usage(self, values: Iterable[ModelUsageDelta]) -> None:
        for value in values:
            self.add_model_usage(value)

    def freeze(self) -> StepResult:
        if self._frozen:
            raise RuntimeError("StepResultBuilder is already frozen")
        self._frozen = True
        return StepResult(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            step_no=self.step_no,
            virtual_time=self.virtual_time,
            agents=tuple(sorted(self._agents, key=lambda value: value.agent_key)),
            conversations=tuple(
                sorted(self._conversations, key=lambda value: str(value.conversation_id))
            ),
            memory_deltas=tuple(
                sorted(self._memory_deltas, key=lambda value: (value.sequence, str(value.event_id)))
            ),
            schedule_revisions=tuple(
                sorted(
                    self._schedule_revisions,
                    key=lambda value: (value.sequence, str(value.revision_id)),
                )
            ),
            domain_events=tuple(
                sorted(self._domain_events, key=lambda value: (value.sequence, str(value.event_id)))
            ),
            committed_model_usage=tuple(
                sorted(self._model_usage, key=lambda value: str(value.logical_call_id))
            ),
        )
