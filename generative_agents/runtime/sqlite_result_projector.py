"""把完整步骤结果幂等投影到 SQLite 查询表。"""

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
    RunStepEffect,
)
from generative_agents.status import (
    MemoryState,
    ResultCompleteness,
    WORKER_OWNED_RUN_STATUSES,
)

from .frame_store import StoredFrame
from .results import ActivityKind, MemoryDeltaKind, StepResult


class ResultProjectionError(RuntimeError):
    """StepResult 无法以幂等方式写入 SQLite 查询投影。"""

    pass


class SqliteResultProjector:
    """帧、检查点、查询投影提交链中的最后一个短事务。"""

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        projection_version: str = "ga-result-v1",
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。
            projection_version: 查询投影协议版本，用于识别需要重建的不兼容结果。 类型：`str`。 默认值：`'ga-result-v1'`。

        返回:
            无返回值。
        """
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
        """原子提交单步查询投影，并返回更新后的结果版本号。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
            frame: 当前仿真步已经落盘且内容不可变的帧记录。 类型：`StoredFrame`。
            checkpoint_path: 当前步骤对应的检查点目录；未生成检查点时为 `None`。 类型：`Path | None`。
            allow_reconcile: 是否启用或满足`allow``reconcile`条件。 类型：`bool`。 默认值：`False`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            ResultProjectionError: 当底层操作报告该异常条件时抛出。

        说明:
            数据库事务最后才推进 available_step。读端因此不会看到只有帧文件、尚无完整查询投影的半提交步骤。
        """
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
                or run.status not in WORKER_OWNED_RUN_STATUSES
            ):
                raise ResultProjectionError(
                    "stale attempt cannot project a result step"
                )
            summary = session.get(RunResultSummary, run.id)
            if summary is None:
                summary = RunResultSummary(
                    run_id=run.id,
                    available_step=0,
                    result_state=ResultCompleteness.EMPTY.value,
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
            wire_effects = {
                item["effect_id"]: item for item in result.to_dict().get("effects", ())
            }
            for effect in result.effects:
                wire = wire_effects[str(effect.effect_id)]
                session.add(
                    RunStepEffect(
                        run_id=run.id,
                        effect_id=str(effect.effect_id),
                        step_no=result.step_no,
                        sequence_no=effect.sequence,
                        virtual_time=result.virtual_time,
                        effect_type=effect.kind.value,
                        primary_agent_key=(
                            effect.agent_keys[0] if effect.agent_keys else None
                        ),
                        agent_keys_json=list(effect.agent_keys),
                        payload_json=dict(wire.get("payload") or {}),
                        source_effect_id=(
                            str(effect.source_effect_id)
                            if effect.source_effect_id
                            else None
                        ),
                        skill_name=effect.skill_name,
                        skill_revision=effect.skill_revision,
                        call_id=str(effect.call_id) if effect.call_id else None,
                    )
                )
            forced_agent_keys = {
                key
                for conversation in result.conversations
                for key in conversation.participant_agent_keys
            }
            forced_agent_keys.update(delta.agent_key for delta in result.memory_deltas)
            forced_agent_keys.update(
                item.agent_key for item in result.schedule_revisions
            )
            forced_agent_keys.update(
                key for event in result.domain_events for key in event.agent_keys
            )
            project_all_agents = (
                result.step_no % definition.results.agent_step_projection_interval_steps
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

            new_conversation_count = 0
            for conversation in result.conversations:
                new_conversation_count += int(
                    self._conversation(session, run, result, conversation)
                )
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
                        primary_agent_key=(
                            event.agent_keys[0] if event.agent_keys else None
                        ),
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
            summary.conversation_count += new_conversation_count
            summary.message_count += message_count
            summary.memory_count += memory_created
            summary.model_call_count += logical_calls
            summary.model_retry_count += retries
            summary.result_state = (
                ResultCompleteness.COMPLETE.value
                if result.step_no >= run.requested_steps
                else ResultCompleteness.PARTIAL.value
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
        """获取智能体摘要。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run_id: 仿真运行的唯一标识。
            agent_key: 智能体在当前实验或运行中的稳定唯一键。
            agent: 参与当前操作的智能体实例。

        返回:
            返回 `RunAgentSummary` 类型的处理结果。
        """
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
    def _conversation(session, run, result, conversation) -> bool:
        """执行`conversation`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run: 当前读取、控制、投影或生成产物的仿真运行记录。
            result: 当前仿真步或上游组件产生的结构化结果。
            conversation: 当前步骤的对话上下文或已经完成的会话记录。

        返回:
            无返回值。

        异常:
            ResultProjectionError: 当底层操作报告该异常条件时抛出。
        """
        initiator, responder = conversation.participant_agent_keys
        participants = tuple(sorted((initiator, responder)))
        if participants[0] == participants[1]:
            raise ResultProjectionError(
                "conversation requires two distinct participants"
            )
        conversation_id = str(conversation.conversation_id)
        stored = session.get(RunConversation, conversation_id)
        is_new = stored is None
        if stored is None:
            stored = RunConversation(
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
            session.add(stored)
            for agent_key in participants:
                session.add(
                    RunConversationParticipant(
                        run_id=run.id,
                        conversation_id=conversation_id,
                        agent_key=agent_key,
                    )
                )
        else:
            stored_participants = tuple(
                sorted((stored.initiator_agent_key, stored.responder_agent_key))
            )
            if stored.run_id != run.id or stored_participants != participants:
                raise ResultProjectionError(
                    "conversation thread identity conflicts with stored participants"
                )
            stored.end_step = result.step_no
            stored.ended_at = result.virtual_time
            stored.duration_minutes = (stored.duration_minutes or 0) + (
                conversation.duration_minutes or 0
            )
            stored.message_count += len(conversation.messages)
            stored.summary = conversation.summary or stored.summary
            stored.ended_reason = conversation.ended_reason or stored.ended_reason
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
        if is_new:
            edge.conversation_count += 1
        edge.message_count += len(conversation.messages)
        edge.duration_minutes += conversation.duration_minutes or 0
        edge.last_conversation_at = result.virtual_time
        for agent_key in participants:
            agent_summary = session.get(RunAgentSummary, (run.id, agent_key))
            if agent_summary is not None:
                if is_new:
                    agent_summary.conversation_count += 1
                agent_summary.message_count += len(conversation.messages)
        return is_new

    @staticmethod
    def _memory(session, run_id, result, delta) -> None:
        """执行记忆的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run_id: 仿真运行的唯一标识。
            result: 当前仿真步或上游组件产生的结构化结果。
            delta: 当前步骤产生的一条增量事实。

        返回:
            无返回值。

        异常:
            ResultProjectionError: 当底层操作报告该异常条件时抛出。
        """
        memory = session.get(RunMemoryEvent, (run_id, delta.agent_key, delta.memory_id))
        if delta.kind == MemoryDeltaKind.CREATED:
            if memory is None:
                session.add(
                    RunMemoryEvent(
                        run_id=run_id,
                        agent_key=delta.agent_key,
                        memory_node_id=delta.memory_id,
                        memory_type=delta.memory_type,
                        origin="RUN",
                        state=MemoryState.ACTIVE.value,
                        description=delta.description,
                        poignancy=delta.poignancy,
                        created_step=result.step_no,
                        created_at=delta.created_at or result.virtual_time,
                        last_accessed_step=result.step_no,
                        last_accessed_at=result.virtual_time,
                        expires_at=delta.expires_at,
                        evidence_node_ids_json=list(delta.evidence_memory_ids),
                        supersedes_memory_node_id=delta.supersedes_memory_id,
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
            states = {
                MemoryDeltaKind.EXPIRED: MemoryState.EXPIRED,
                MemoryDeltaKind.EVICTED: MemoryState.EVICTED,
                MemoryDeltaKind.SUPERSEDED: MemoryState.SUPERSEDED,
                MemoryDeltaKind.INVALIDATED: MemoryState.INVALIDATED,
            }
            memory.state = states[delta.kind].value
            memory.removed_step = result.step_no
            memory.removed_at = result.virtual_time
            memory.superseded_by_memory_node_id = delta.replacement_memory_id
            memory.invalidated_reason = delta.reason

    @staticmethod
    def _schedule(session, run_id, result, record) -> None:
        """执行日程的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run_id: 仿真运行的唯一标识。
            result: 当前仿真步或上游组件产生的结构化结果。
            record: 当前读取、校验、投影或序列化的持久化记录。

        返回:
            无返回值。
        """
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
            source_event_id=(
                str(record.source_event_id) if record.source_event_id else None
            ),
            content_hash=record.content_hash,
            items_json=[dict(item) for item in record.schedule],
        )
        session.add(value)
        agent_summary = session.get(RunAgentSummary, (run_id, record.agent_key))
        if agent_summary is not None:
            agent_summary.latest_schedule_revision_id = value.id

    @staticmethod
    def _default_capabilities() -> dict:
        """执行`default``capabilities`的内部处理，供当前模块或类复用。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
