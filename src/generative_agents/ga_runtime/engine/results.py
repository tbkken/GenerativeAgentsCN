"""Collect one iteration into an immutable protocol StepResult."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Iterable, Any
from uuid import UUID
from generative_agents.ga_protocol.schemas.facts import AgentStepResult, ConversationRecord, DomainEventRecord, MemoryDelta, ModelUsageDelta, ScheduleRevisionRecord, StepEffectRecord, StepResult

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

    @property
    def domain_events(self) -> tuple[DomainEventRecord, ...]:
        """Facts already produced in this Step, for bounded object observations."""
        return tuple(self._domain_events)

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
