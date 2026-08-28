"""单个已提交仿真步使用的完整、不可变结果信封。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

from generative_agents.status import MemoryDeltaKind


class ActivityKind(StrEnum):
    """单个智能体在一步结束时可记录的动作类别。"""

    REST = "REST"
    CHAT = "CHAT"
    MOVING = "MOVING"
    OTHER = "OTHER"


class StepEffectKind(StrEnum):
    """单个仿真步执行过程中产生的规范事实类型。"""

    ACTION_SELECTED = "ACTION_SELECTED"
    EVENT_PERCEIVED = "EVENT_PERCEIVED"
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_ACCESSED = "MEMORY_ACCESSED"
    MEMORY_EXPIRED = "MEMORY_EXPIRED"
    MEMORY_EVICTED = "MEMORY_EVICTED"
    MEMORY_SUPERSEDED = "MEMORY_SUPERSEDED"
    MEMORY_INVALIDATED = "MEMORY_INVALIDATED"
    REFLECTION_CREATED = "REFLECTION_CREATED"
    SCHEDULE_REVISED = "SCHEDULE_REVISED"
    DOMAIN_EVENT = "DOMAIN_EVENT"
    SKILL_EXECUTED = "SKILL_EXECUTED"


@dataclass(frozen=True, slots=True)
class ActionSnapshot:
    """智能体本步最终动作的文本、表情和可选对象状态。"""

    description: str
    emoji: str | None = None
    object_description: str | None = None


@dataclass(frozen=True, slots=True)
class AgentStepResult:
    """单个智能体在当前步骤的位置、剩余路径和动作结果。"""

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
    """一次对话中带有稳定说话人键的单条消息。"""

    message_id: UUID
    sequence: int
    speaker_agent_key: str
    content: str


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """发生在本步的完整对话及参与者、地点和持续时间。"""

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
    """本步对某个智能体长期记忆执行的新增、访问或删除事实。"""

    event_id: UUID
    sequence: int
    agent_key: str
    memory_id: str
    kind: MemoryDeltaKind
    memory_type: str
    description: str | None = None
    poignancy: float | None = None
    source_event_id: UUID | None = None
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    address: tuple[str, ...] = ()
    created_at: datetime | None = None
    expires_at: datetime | None = None
    evidence_memory_ids: tuple[str, ...] = ()
    replacement_memory_id: str | None = None
    supersedes_memory_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleRevisionRecord:
    """本步触发的日程变更及变更后的结构化内容。"""

    revision_id: UUID
    sequence: int
    agent_key: str
    reason: str
    source_event_id: UUID | None
    content_hash: str
    schedule: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DomainEventRecord:
    """需要持久化和回放的通用领域事件。"""

    event_id: UUID
    sequence: int
    event_type: str
    agent_keys: tuple[str, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StepEffectRecord:
    """步骤账本中的一条不可变认知或世界副作用。

    记忆、日程和领域记录仍属于专用查询投影；本记录是检查点、回放以及未来技能驱动执行
    共同依赖的因果历史。
    """

    effect_id: UUID
    sequence: int
    kind: StepEffectKind
    agent_keys: tuple[str, ...]
    payload: Mapping[str, Any]
    source_effect_id: UUID | None = None
    skill_name: str | None = None
    skill_revision: str | None = None
    call_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ModelUsageDelta:
    """一次逻辑模型调用的尝试次数、Token 与耗时增量。"""

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
    """执行 的`deterministic``record``id`操作。

    参数:
        run_id: 仿真运行的唯一标识。 类型：`UUID`。
        step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
        kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。
        key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

    返回:
        返回 `UUID` 类型的处理结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """

    if step_no < 1:
        raise ValueError("step_no must be greater than zero")
    return uuid5(run_id, f"{step_no}:{kind}:{key}")


def _wire_value(value: Any) -> Any:
    """执行`wire``value`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`Any`。

    返回:
        返回 `Any` 类型的处理结果。
    """
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
    """一个完整仿真步的不可变事实信封，是所有投影器的共同输入。"""

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
    effects: tuple[StepEffectRecord, ...] = ()

    def __post_init__(self) -> None:
        """完成数据类初始化后的规范化与不变量校验。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.step_no < 1:
            raise ValueError("step_no must be greater than zero")
        if self.virtual_time.tzinfo is None:
            raise ValueError("virtual_time must be timezone-aware")
        agent_keys = [item.agent_key for item in self.agents]
        if len(agent_keys) != len(set(agent_keys)):
            raise ValueError("agents must contain at most one result per agent_key")
        if not self.effects:
            object.__setattr__(self, "effects", self._project_effects())

    def _project_effects(self) -> tuple[StepEffectRecord, ...]:
        """执行`project``effects`的内部处理，供当前模块或类复用。

        返回:
            返回按接口约定组织的结果集合。
        """

        effects: list[StepEffectRecord] = []
        memory_effects = {
            MemoryDeltaKind.CREATED: StepEffectKind.MEMORY_CREATED,
            MemoryDeltaKind.ACCESSED: StepEffectKind.MEMORY_ACCESSED,
            MemoryDeltaKind.EXPIRED: StepEffectKind.MEMORY_EXPIRED,
            MemoryDeltaKind.EVICTED: StepEffectKind.MEMORY_EVICTED,
            MemoryDeltaKind.SUPERSEDED: StepEffectKind.MEMORY_SUPERSEDED,
            MemoryDeltaKind.INVALIDATED: StepEffectKind.MEMORY_INVALIDATED,
        }
        for item in self.memory_deltas:
            payload = {
                "memory_id": item.memory_id,
                "memory_type": item.memory_type,
                "description": item.description,
                "poignancy": item.poignancy,
                "subject": item.subject,
                "predicate": item.predicate,
                "object": item.object,
                "address": list(item.address),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "evidence_memory_ids": list(item.evidence_memory_ids),
                "replacement_memory_id": item.replacement_memory_id,
                "supersedes_memory_id": item.supersedes_memory_id,
                "reason": item.reason,
            }
            effects.append(
                StepEffectRecord(
                    effect_id=item.event_id,
                    sequence=item.sequence,
                    kind=memory_effects[item.kind],
                    agent_keys=(item.agent_key,),
                    payload=payload,
                    source_effect_id=item.source_event_id,
                )
            )
            if (
                item.kind == MemoryDeltaKind.CREATED
                and item.memory_type.upper() == "THOUGHT"
            ):
                effects.append(
                    StepEffectRecord(
                        effect_id=deterministic_record_id(
                            self.run_id,
                            self.step_no,
                            "reflection",
                            f"{item.agent_key}:{item.memory_id}",
                        ),
                        sequence=item.sequence,
                        kind=StepEffectKind.REFLECTION_CREATED,
                        agent_keys=(item.agent_key,),
                        payload=payload,
                        source_effect_id=item.event_id,
                    )
                )
        for item in self.schedule_revisions:
            effects.append(
                StepEffectRecord(
                    effect_id=item.revision_id,
                    sequence=item.sequence,
                    kind=StepEffectKind.SCHEDULE_REVISED,
                    agent_keys=(item.agent_key,),
                    payload={
                        "reason": item.reason,
                        "content_hash": item.content_hash,
                        "schedule": list(item.schedule),
                    },
                    source_effect_id=item.source_event_id,
                )
            )
        for item in self.domain_events:
            effects.append(
                StepEffectRecord(
                    effect_id=item.event_id,
                    sequence=item.sequence,
                    kind=StepEffectKind.DOMAIN_EVENT,
                    agent_keys=item.agent_keys,
                    payload={"event_type": item.event_type, **dict(item.payload)},
                )
            )
        return tuple(
            sorted(
                effects,
                key=lambda value: (
                    value.sequence,
                    value.kind.value,
                    str(value.effect_id),
                ),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """执行 `StepResult` 的`to``dict`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return _wire_value(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StepResult":
        """执行 `StepResult` 的`from``dict`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`Mapping[str, Any]`。

        返回:
            返回 `'StepResult'` 类型的处理结果。
        """

        def optional_uuid(item):
            """执行 `StepResult` 的`optional``uuid`操作。

            参数:
                item: 当前操作使用的`item`。

            返回:
                返回函数计算得到的结果。
            """
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
                    subject=item.get("subject"),
                    predicate=item.get("predicate"),
                    object=item.get("object"),
                    address=tuple(item.get("address", ())),
                    created_at=(
                        datetime.fromisoformat(item["created_at"])
                        if item.get("created_at")
                        else None
                    ),
                    expires_at=(
                        datetime.fromisoformat(item["expires_at"])
                        if item.get("expires_at")
                        else None
                    ),
                    evidence_memory_ids=tuple(item.get("evidence_memory_ids", ())),
                    replacement_memory_id=item.get("replacement_memory_id"),
                    supersedes_memory_id=item.get("supersedes_memory_id"),
                    reason=item.get("reason"),
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
            effects=tuple(
                StepEffectRecord(
                    effect_id=UUID(item["effect_id"]),
                    sequence=int(item["sequence"]),
                    kind=StepEffectKind(item["kind"]),
                    agent_keys=tuple(item.get("agent_keys", ())),
                    payload=dict(item.get("payload", {})),
                    source_effect_id=optional_uuid(item.get("source_effect_id")),
                    skill_name=item.get("skill_name"),
                    skill_revision=item.get("skill_revision"),
                    call_id=optional_uuid(item.get("call_id")),
                )
                for item in value.get("effects", ())
            ),
        )


@dataclass(slots=True)
class StepResultBuilder:
    """单个仿真步内部唯一允许变更的结果收集器。"""

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
    _effects: list[StepEffectRecord] = field(default_factory=list)
    _frozen: bool = False

    def _append(self, target: list[Any], value: Any) -> None:
        """执行`append`的内部处理，供当前模块或类复用。

        参数:
            target: 当前操作使用的`target`。 类型：`list[Any]`。
            value: 当前操作使用的`value`。 类型：`Any`。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        if self._frozen:
            raise RuntimeError("StepResultBuilder is already frozen")
        target.append(value)

    def add_agent(self, value: AgentStepResult) -> None:
        """执行 `StepResultBuilder` 的`add`智能体操作。

        参数:
            value: 当前操作使用的`value`。 类型：`AgentStepResult`。

        返回:
            无返回值。
        """
        self._append(self._agents, value)

    def add_conversation(self, value: ConversationRecord) -> None:
        """执行 `StepResultBuilder` 的`add``conversation`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`ConversationRecord`。

        返回:
            无返回值。
        """
        self._append(self._conversations, value)

    def add_memory_delta(self, value: MemoryDelta) -> None:
        """执行 `StepResultBuilder` 的`add`记忆`delta`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`MemoryDelta`。

        返回:
            无返回值。
        """
        self._append(self._memory_deltas, value)

    def add_schedule_revision(self, value: ScheduleRevisionRecord) -> None:
        """执行 `StepResultBuilder` 的`add`日程修订版本操作。

        参数:
            value: 当前操作使用的`value`。 类型：`ScheduleRevisionRecord`。

        返回:
            无返回值。
        """
        self._append(self._schedule_revisions, value)

    def add_domain_event(self, value: DomainEventRecord) -> None:
        """执行 `StepResultBuilder` 的`add``domain`事件操作。

        参数:
            value: 当前操作使用的`value`。 类型：`DomainEventRecord`。

        返回:
            无返回值。
        """
        self._append(self._domain_events, value)

    def add_model_usage(self, value: ModelUsageDelta) -> None:
        """执行 `StepResultBuilder` 的`add`模型`usage`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`ModelUsageDelta`。

        返回:
            无返回值。
        """
        self._append(self._model_usage, value)

    def add_effect(self, value: StepEffectRecord) -> None:
        """执行 `StepResultBuilder` 的`add``effect`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`StepEffectRecord`。

        返回:
            无返回值。
        """
        self._append(self._effects, value)

    def extend_model_usage(self, values: Iterable[ModelUsageDelta]) -> None:
        """执行 `StepResultBuilder` 的`extend`模型`usage`操作。

        参数:
            values: 需要规范化、校验、拼接或批量处理的值集合。 类型：`Iterable[ModelUsageDelta]`。

        返回:
            无返回值。
        """
        for value in values:
            self.add_model_usage(value)

    def freeze(self) -> StepResult:
        """将可变运行态冻结为不可变的步骤结果。

        返回:
            返回 `StepResult` 类型的处理结果。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        if self._frozen:
            raise RuntimeError("StepResultBuilder is already frozen")
        self._frozen = True
        result = StepResult(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            step_no=self.step_no,
            virtual_time=self.virtual_time,
            agents=tuple(sorted(self._agents, key=lambda value: value.agent_key)),
            conversations=tuple(
                sorted(
                    self._conversations, key=lambda value: str(value.conversation_id)
                )
            ),
            memory_deltas=tuple(
                sorted(
                    self._memory_deltas,
                    key=lambda value: (value.sequence, str(value.event_id)),
                )
            ),
            schedule_revisions=tuple(
                sorted(
                    self._schedule_revisions,
                    key=lambda value: (value.sequence, str(value.revision_id)),
                )
            ),
            domain_events=tuple(
                sorted(
                    self._domain_events,
                    key=lambda value: (value.sequence, str(value.event_id)),
                )
            ),
            committed_model_usage=tuple(
                sorted(self._model_usage, key=lambda value: str(value.logical_call_id))
            ),
        )
        if not self._effects:
            return result
        effects = {item.effect_id: item for item in result.effects}
        effects.update({item.effect_id: item for item in self._effects})
        return replace(
            result,
            effects=tuple(
                sorted(
                    effects.values(),
                    key=lambda value: (
                        value.sequence,
                        value.kind.value,
                        str(value.effect_id),
                    ),
                )
            ),
        )
