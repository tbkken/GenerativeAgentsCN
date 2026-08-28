"""把旧领域模型的实际执行结果转换为完整步骤结果账本。"""

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
    """单步结果适配器；在领域事实产生的位置立即捕获事实。"""

    def __init__(self, builder: StepResultBuilder, *, name_to_key: Mapping[str, str]):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            builder: 当前步骤唯一的可变结果收集器，冻结后生成不可变步骤结果。 类型：`StepResultBuilder`。
            name_to_key: 用于稳定定位`name``to`的键。 类型：`Mapping[str, str]`。

        返回:
            无返回值。
        """
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
        """执行 `StepResultCollector` 的`capture`智能体操作。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            agent: 参与当前操作的智能体实例。
            from_coord: 智能体开始当前移动区间时的观测坐标。
            outcome: 当前步骤或交互实际产生的结构化结果。 类型：`Mapping`。
            executed_path: `executed`对应的文件系统路径。 默认值：`None`。
            planned_path: `planned`对应的文件系统路径。 默认值：`None`。
            remaining_path: `remaining`对应的文件系统路径。 默认值：`None`。

        返回:
            无返回值。
        """
        plan = outcome.get("plan") or {}
        info = outcome.get("info") or {}
        planned_path = tuple(
            tuple(coord)
            for coord in (plan.get("path") if planned_path is None else planned_path)
            or ()
        )
        observed_path = tuple(
            tuple(coord)
            for coord in (planned_path if executed_path is None else executed_path)
            or ()
        )
        remaining_path = tuple(tuple(coord) for coord in (remaining_path or ()))
        to_coord = tuple(agent.coord)
        event = agent.get_event()
        description = event.get_describe() if event else ""
        predicate = event.predicate if event else ""
        if observed_path and tuple(from_coord) != to_coord:
            activity = ActivityKind.MOVING
        elif predicate in {"对话", "chat", "聊天"}:
            activity = ActivityKind.CHAT
        elif predicate in {"等待", "wait", "休息", "睡眠", "睡觉"}:
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
        for raw_event in outcome.get("events") or ():
            self._capture_event(raw_event)

    @staticmethod
    def _event_payload(event) -> dict | None:
        """执行事件载荷的内部处理，供当前模块或类复用。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
        """
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
        """执行`decision`运行上下文的内部处理，供当前模块或类复用。

        参数:
            plan: 智能体当前计划或等待执行的计划片段。 类型：`Mapping`。
            info: 当前事件、步骤或调用的结构化附加信息。 类型：`Mapping`。
            planned_path: `planned`对应的文件系统路径。 默认值：`None`。
            remaining_path: `remaining`对应的文件系统路径。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """

        if planned_path is None:
            planned_path = tuple(tuple(coord) for coord in (plan.get("path") or ()))

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
        """执行`capture`事件的内部处理，供当前模块或类复用。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。 类型：`Mapping`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        kind = event.get("kind")
        if kind == "conversation":
            self._sequences["conversation"] += 1
            sequence = self._sequences["conversation"]
            raw_conversation_id = event.get("conversation_id")
            conversation_id = (
                UUID(str(raw_conversation_id))
                if raw_conversation_id
                else deterministic_record_id(
                    self.builder.run_id,
                    self.builder.step_no,
                    "conversation",
                    str(sequence),
                )
            )
            messages = []
            first_message_sequence = int(event.get("message_sequence") or 1)
            for message_offset, (speaker, content) in enumerate(event["messages"]):
                message_sequence = first_message_sequence + message_offset
                speaker_key = self._name_to_key.get(speaker, speaker)
                messages.append(
                    ConversationMessage(
                        message_id=deterministic_record_id(
                            self.builder.run_id,
                            self.builder.step_no,
                            "message",
                            f"{conversation_id}:{message_sequence}",
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
                {
                    "conversation_id": str(conversation_id),
                    "message_count": len(messages),
                },
            )
        elif kind == "memory":
            self._sequences["memory"] += 1
            sequence = self._sequences["memory"]
            try:
                delta_kind = MemoryDeltaKind(
                    event.get("memory_kind", MemoryDeltaKind.CREATED.value)
                )
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
                    address=tuple(
                        semantic_event.get("address") or event.get("address") or ()
                    ),
                    created_at=(
                        datetime.fromisoformat(created_at) if created_at else None
                    ),
                    expires_at=(
                        datetime.fromisoformat(expires_at) if expires_at else None
                    ),
                    evidence_memory_ids=tuple(event.get("evidence_memory_ids") or ()),
                    replacement_memory_id=event.get("replacement_memory_id"),
                    supersedes_memory_id=event.get("supersedes_memory_id"),
                    reason=event.get("reason"),
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
                        self.builder.run_id,
                        self.builder.step_no,
                        "schedule",
                        str(sequence),
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
                    "input_text": event.get("input_text"),
                    "output_text": event.get("output_text"),
                    "execution_source": event.get("execution_source"),
                    "source_type": "AGENT_COGNITION",
                    "trace": list(event.get("trace") or ()),
                },
                key=(
                    f"skill:{event['agent_key']}:{event['skill_name']}:"
                    f"{self._sequences['effect'] + 1}"
                ),
                skill_name=event["skill_name"],
                skill_revision=event.get("skill_revision"),
            )
        elif kind == "world_domain_event":
            structured_payload = event.get("structured_payload")
            if not isinstance(structured_payload, Mapping) or not structured_payload:
                raise ValueError(
                    "world_domain_event requires a non-empty structured_payload"
                )
            subject = str(event.get("subject") or "").strip()
            predicate = str(event.get("predicate") or "").strip()
            object_value = str(event.get("object") or "").strip()
            if not subject or not predicate or not object_value:
                raise ValueError(
                    "world_domain_event requires Event(subject, predicate, object)"
                )
            self._add_domain_event(
                str(event.get("event_type") or "WORLD_CHANGED"),
                tuple(event.get("agent_keys") or ()),
                {
                    "title": str(
                        structured_payload.get("description")
                        or f"{subject}{predicate}{object_value}"
                    ),
                    "detail": f"{subject} / {predicate} / {object_value}",
                    "location": ":".join(
                        str(part)
                        for part in (
                            structured_payload.get("after_address")
                            or structured_payload.get("address")
                            or ()
                        )
                        if str(part)
                    ),
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_value,
                    "structured_payload": dict(structured_payload),
                },
            )

    def capture_event(self, event: Mapping) -> None:
        """执行 `StepResultCollector` 的`capture`事件操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。 类型：`Mapping`。

        返回:
            无返回值。
        """

        self._capture_event(event)

    def _add_domain_event(self, event_type: str, agent_keys, payload) -> None:
        """执行`add``domain`事件的内部处理，供当前模块或类复用。

        参数:
            event_type: 模型轨迹事件类型筛选值；为空时不按事件类型过滤。 类型：`str`。
            agent_keys: 需要查询、关联或提交结果的智能体稳定键集合。
            payload: 待处理的结构化载荷；必需字段由当前操作的输入协议定义。

        返回:
            无返回值。
        """
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
        """执行`add``effect`的内部处理，供当前模块或类复用。

        参数:
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`StepEffectKind`。
            agent_keys: 需要查询、关联或提交结果的智能体稳定键集合。
            payload: 待处理的结构化载荷；必需字段由当前操作的输入协议定义。
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
            source_effect_id: `source``effect`的唯一标识。 类型：`UUID | None`。 默认值：`None`。
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str | None`。 默认值：`None`。
            skill_revision: 当前运行固定使用的技能修订标识。 类型：`str | None`。 默认值：`None`。

        返回:
            无返回值。
        """
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
        """将可变运行态冻结为不可变的步骤结果。

        返回:
            返回 `StepResult` 类型的处理结果。
        """
        return self.builder.freeze()
