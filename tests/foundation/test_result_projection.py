"""基础能力回归测试：覆盖 ``test_result_projection`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import (
    RunAgentStep,
    RunAgentSummary,
    RunConversation,
    RunMemoryEvent,
    RunMessage,
    RunModelUsage,
    RunRelationshipEdge,
    RunResultSummary,
    RunStep,
    RunStepEffect,
)
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.result_projector import SqliteResultProjector
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    ConversationMessage,
    ConversationRecord,
    DomainEventRecord,
    MemoryDelta,
    MemoryDeltaKind,
    ModelUsageDelta,
    ScheduleRevisionRecord,
    StepResultBuilder,
    deterministic_record_id,
)
from tests.support import brain_selection_for_database, publish_user_map
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
    ModelTraceWriter,
)
from generative_agents.runtime.trace_projector import ModelTraceProjector
from generative_agents.services.runs import RunService
from generative_agents.services.results import ResultQueryService
from generative_agents.services.errors import ServiceError


def _publish(service, definition: ExperimentDefinition):
    """为本测试模块封装 ``_publish`` 辅助步骤，减少重复的场景搭建代码。"""
    map_revision = publish_user_map(service.database, world=definition.world)
    created = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
        map_revision_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    draft = service.get_draft(created["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = created["experiment_key"]
    payload["world"] = draft["definition"]["world"]
    payload["engine"] = draft["definition"]["engine"]
    draft = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    revision = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    return created, revision


def test_complete_step_frame_projects_all_query_facts_idempotently(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_complete_step_frame_projects_all_query_facts_idempotently`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=999, pid_create_time=1.0)
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    virtual_time = datetime(2026, 2, 13, 8, 0, tzinfo=timezone.utc)
    conversation_id = deterministic_record_id(run_id, 1, "conversation", "a:b:1")
    memory_event_id = deterministic_record_id(run_id, 1, "memory", "a:m1:1")
    domain_event_id = deterministic_record_id(run_id, 1, "domain", "conversation:1")
    builder = StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=1,
        virtual_time=virtual_time,
    )
    for key, start in (("a-agent", 0), ("b-agent", 2)):
        builder.add_agent(
            AgentStepResult(
                agent_key=key,
                from_coord=(start, 0),
                to_coord=(start + 1, 0),
                path=((start, 0), (start + 1, 0)),
                action=ActionSnapshot(description="walk and chat", emoji="💬"),
                activity_kind=ActivityKind.CHAT,
                location=("ville", "cafe"),
                currently="testing",
                decision_context=(
                    {
                        "perceptions": [{"node_id": "seen-1", "content": "B arrived"}],
                        "schedule": {"08:00~08:10": "chat"},
                        "action": {"event": "walk and chat"},
                        "path": [[start, 0], [start + 1, 0]],
                        "memory_counts": {"event": 1, "chat": 0, "thought": 0},
                    }
                    if key == "a-agent"
                    else {}
                ),
            )
        )
    builder.add_conversation(
        ConversationRecord(
            conversation_id=conversation_id,
            participant_agent_keys=("a-agent", "b-agent"),
            location=("ville", "cafe"),
            messages=(
                ConversationMessage(
                    message_id=deterministic_record_id(run_id, 1, "message", "c1:1"),
                    sequence=1,
                    speaker_agent_key="a-agent",
                    content="hello",
                ),
                ConversationMessage(
                    message_id=deterministic_record_id(run_id, 1, "message", "c1:2"),
                    sequence=2,
                    speaker_agent_key="b-agent",
                    content="hi",
                ),
            ),
            summary="greeting",
            ended_reason="complete",
            duration_minutes=1,
        )
    )
    builder.add_memory_delta(
        MemoryDelta(
            event_id=memory_event_id,
            sequence=1,
            agent_key="a-agent",
            memory_id="memory-1",
            kind=MemoryDeltaKind.CREATED,
            memory_type="CHAT",
            description="talked to b",
            poignancy=3,
        )
    )
    builder.add_schedule_revision(
        ScheduleRevisionRecord(
            revision_id=deterministic_record_id(run_id, 1, "schedule", "a:1"),
            sequence=1,
            agent_key="a-agent",
            reason="conversation",
            source_event_id=domain_event_id,
            content_hash="1" * 64,
            schedule=({"start": 0, "duration": 10, "description": "chat"},),
        )
    )
    builder.add_domain_event(
        DomainEventRecord(
            event_id=domain_event_id,
            sequence=1,
            event_type="CONVERSATION",
            agent_keys=("a-agent", "b-agent"),
            payload={
                "title": "A 与 B 对话",
                "importance_score": 2,
                "source_type": "conversation",
                "source_id": str(conversation_id),
            },
        )
    )
    builder.add_model_usage(
        ModelUsageDelta(
            logical_call_id=uuid4(),
            purpose="chat",
            provider="vllm",
            model="test-model",
            physical_attempts=2,
            total_tokens=10,
            latency_ms=20,
        )
    )
    result = builder.freeze()
    frame = FrameStore(RunPaths.under(var_dir, run_id)).write(result)
    projector = SqliteResultProjector(database, var_dir=var_dir)

    version = projector.commit_step(result, frame=frame, checkpoint_path=None)
    repeated_version = projector.commit_step(result, frame=frame, checkpoint_path=None)

    assert version == repeated_version == 1
    with database.session_factory() as session:
        summary = session.get(RunResultSummary, run["run_id"])
        assert summary.available_step == 1
        assert summary.conversation_count == 1
        assert summary.message_count == 2
        assert summary.memory_count == 1
        assert summary.model_retry_count == 1
        assert session.scalar(select(func.count()).select_from(RunStep)) == 1
        assert session.scalar(select(func.count()).select_from(RunConversation)) == 1
        assert session.scalar(select(func.count()).select_from(RunMessage)) == 2
        assert session.get(RunResultSummary, run["run_id"]).conversation_count == 1
        assert session.scalar(select(func.count()).select_from(RunStepEffect)) == 3
        assert session.get(RunMemoryEvent, (run["run_id"], "a-agent", "memory-1")).state == "ACTIVE"
        edge = session.get(RunRelationshipEdge, (run["run_id"], "a-agent", "b-agent"))
        assert edge.conversation_count == 1
        assert session.get(RunAgentSummary, (run["run_id"], "a-agent")).message_count == 2
        assert session.get(RunAgentStep, (run["run_id"], 1, "a-agent")).decision_context_json[
            "perceptions"
        ][0]["node_id"] == "seen-1"

    queries = ResultQueryService(database)
    summary_view = queries.summary(run["run_id"])
    assert summary_view["available_step"] == 1
    assert summary_view["counts"]["conversations"] == 1
    assert summary_view["conversation_network"]["edges"][0]["agent_a"] == "a-agent"
    assert queries.timeline(run["run_id"])["steps"][0]["step_no"] == 1
    agent_list = queries.agents(run["run_id"])["items"]
    assert {item["agent_key"] for item in agent_list} == {
        "a-agent",
        "b-agent",
    }
    listed_a = next(item for item in agent_list if item["agent_key"] == "a-agent")
    assert listed_a["plan_count"] == 1 and listed_a["event_count"] == 1
    assert listed_a["portrait_url"].endswith("a-agent/portrait.png")
    agent_workspace = queries.agent(run["run_id"], "a-agent")
    assert agent_workspace["latest_schedule"]["reason"] == "conversation"
    assert agent_workspace["content_counts"] == {
        "plans": 1,
        "actions": 1,
        "events": 1,
        "conversations": 1,
        "memories": 1,
        "state_changes": 0,
    }
    assert agent_workspace["actions"][0]["decision_context"]["perceptions"][0][
        "node_id"
    ] == "seen-1"
    assert agent_workspace["events"][0]["payload"]["title"] == "A 与 B 对话"
    assert agent_workspace["conversations"][0]["summary"] == "greeting"
    assert agent_workspace["memories"][0]["description"] == "talked to b"
    conversation_list = queries.conversations(run["run_id"], query="hello")
    assert conversation_list["items"][0]["conversation_id"] == str(conversation_id)
    assert len(queries.conversation(run["run_id"], str(conversation_id))["messages"]) == 2
    assert queries.memories(run["run_id"], agent_key="a-agent")["items"][0][
        "description"
    ] == "talked to b"
    assert queries.operations(run["run_id"])["attempts"][0]["attempt_no"] == 1

    isolated_experiment, isolated_revision = _publish(service, publishable_definition)
    isolated = RunService(database, var_dir=var_dir).create_from_published(
        isolated_experiment["id"], isolated_revision["id"]
    )
    assert queries.summary(isolated["run_id"])["result_state"] == "EMPTY"
    assert queries.conversations(isolated["run_id"])["items"] == []
    try:
        queries.conversation(isolated["run_id"], str(conversation_id))
    except ServiceError as exc:
        assert exc.code == "CONVERSATION_NOT_FOUND"
    else:  # pragma: no cover - an isolation failure must never be silent
        raise AssertionError("another run's conversation leaked across the run boundary")


def test_conversation_projection_appends_messages_to_one_cross_step_thread(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var-thread"
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=998, pid_create_time=1.0)
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    conversation_id = deterministic_record_id(run_id, 1, "conversation", "thread")
    projector = SqliteResultProjector(database, var_dir=var_dir)

    for step_no, speaker, content in (
        (1, "a-agent", "第一句话"),
        (2, "b-agent", "第二句话"),
    ):
        virtual_time = datetime(
            2026, 2, 13, 8, (step_no - 1) * 10, tzinfo=timezone.utc
        )
        builder = StepResultBuilder(
            run_id=run_id,
            attempt_id=attempt_id,
            step_no=step_no,
            virtual_time=virtual_time,
        )
        for agent_key, x in (("a-agent", 0), ("b-agent", 1)):
            builder.add_agent(
                AgentStepResult(
                    agent_key=agent_key,
                    from_coord=(x, 0),
                    to_coord=(x, 0),
                    path=(),
                    action=ActionSnapshot(description="说话"),
                    activity_kind=ActivityKind.CHAT,
                    location=("ville", "cafe"),
                )
            )
        builder.add_conversation(
            ConversationRecord(
                conversation_id=conversation_id,
                participant_agent_keys=("a-agent", "b-agent"),
                location=("ville", "cafe"),
                messages=(
                    ConversationMessage(
                        message_id=deterministic_record_id(
                            run_id, step_no, "message", f"thread:{step_no}"
                        ),
                        sequence=step_no,
                        speaker_agent_key=speaker,
                        content=content,
                    ),
                ),
                summary=content,
                duration_minutes=10,
            )
        )
        result = builder.freeze()
        frame = FrameStore(RunPaths.under(var_dir, run_id)).write(result)
        projector.commit_step(result, frame=frame, checkpoint_path=None)

    with database.session_factory() as session:
        conversation = session.get(RunConversation, str(conversation_id))
        assert conversation.start_step == 1 and conversation.end_step == 2
        assert conversation.message_count == 2
        assert session.scalar(select(func.count()).select_from(RunConversation)) == 1
        assert session.scalar(select(func.count()).select_from(RunMessage)) == 2
        edge = session.get(
            RunRelationshipEdge, (run["run_id"], "a-agent", "b-agent")
        )
        assert edge.conversation_count == 1 and edge.message_count == 2
        assert session.get(
            RunAgentSummary, (run["run_id"], "a-agent")
        ).conversation_count == 1

    detail = ResultQueryService(database).conversation(
        run["run_id"], str(conversation_id)
    )
    assert [message["sequence"] for message in detail["messages"]] == [1, 2]


def test_model_trace_cursor_counts_failed_attempts_and_is_idempotent(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_model_trace_cursor_counts_failed_attempts_and_is_idempotent`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    claimed = LocalRunSchedulerRepository(database).claim_next()
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    writer = ModelTraceWriter(
        RunPaths.under(var_dir, run_id),
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=1,
        capture_payloads=False,
    )
    at = datetime(2026, 2, 13, tzinfo=timezone.utc)
    call_id = uuid4()
    for physical_attempt, status in (
        (1, ModelTraceStatus.FAILED),
        (2, ModelTraceStatus.SUCCEEDED),
    ):
        writer.append(
            ModelTraceEvent(
                event_type=ModelTraceEventType.PHYSICAL_START,
                run_id=run_id,
                attempt_id=attempt_id,
                call_id=call_id,
                step_no=None,
                agent_key="a-agent",
                purpose="chat",
                prompt_key="generate_chat",
                provider="vllm",
                resolved_model="test-model",
                started_at=at,
                ended_at=at,
                latency_ms=0,
                attempt_no=physical_attempt,
                status=ModelTraceStatus.RUNNING,
            )
        )
        writer.append(
            ModelTraceEvent(
                event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
                run_id=run_id,
                attempt_id=attempt_id,
                call_id=call_id,
                step_no=None,
                agent_key="a-agent",
                purpose="chat",
                prompt_key="generate_chat",
                provider="vllm",
                resolved_model="test-model",
                started_at=at,
                ended_at=at,
                latency_ms=physical_attempt * 100,
                attempt_no=physical_attempt,
                status=status,
                prompt_tokens=10 if physical_attempt == 2 else None,
                completion_tokens=5 if physical_attempt == 2 else None,
            )
        )
    writer.append(
        ModelTraceEvent(
            event_type=ModelTraceEventType.LOGICAL_END,
            run_id=run_id,
            attempt_id=attempt_id,
            call_id=call_id,
            step_no=None,
            agent_key="a-agent",
            purpose="chat",
            prompt_key="generate_chat",
            provider="vllm",
            resolved_model="test-model",
            started_at=at,
            ended_at=at,
            latency_ms=300,
            attempt_no=None,
            status=ModelTraceStatus.SUCCEEDED,
        )
    )
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                capabilities_json={},
                projection_version="ga-result-v1",
                result_version=1,
            )
        )
        session.add(
            RunStep(
                run_id=run["run_id"],
                step_no=1,
                attempt_id=claimed.attempt_id,
                virtual_time=at,
                frame_path="runs/test/frames/step-000001.json.gz",
                frame_sha256="0" * 64,
                action_count=0,
                movement_count=0,
                conversation_count=0,
                message_count=0,
                memory_created_count=0,
                memory_accessed_count=0,
                model_logical_calls=0,
                model_retry_count=0,
                active_agent_count=0,
                checkpoint=False,
            )
        )
    projector = ModelTraceProjector(database, var_dir=var_dir)
    relative = writer.path.relative_to(var_dir).as_posix()

    assert projector.project(
        run_id=run["run_id"], attempt_id=claimed.attempt_id, relative_path=relative
    ) == 5
    assert projector.project(
        run_id=run["run_id"], attempt_id=claimed.attempt_id, relative_path=relative
    ) == 5
    with database.session_factory() as session:
        usage = session.get(
            RunModelUsage, (run["run_id"], "chat", "vllm", "test-model")
        )
        assert usage.logical_call_count == 1
        assert usage.successful_call_count == 1
        assert usage.physical_attempt_count == 2
        assert usage.retry_count == 1
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        summary = session.get(RunResultSummary, run["run_id"])
        assert summary.model_call_count == 1
        assert summary.model_retry_count == 1
        step = session.get(RunStep, (run["run_id"], 1))
        assert step.model_logical_calls == 1
        assert step.model_retry_count == 1
        # Re-projecting from an unchanged cursor must not double-count.
        assert summary.result_version == 2


def test_model_trace_projection_accepts_an_attempt_with_no_trace_file(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_model_trace_projection_accepts_an_attempt_with_no_trace_file`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    claimed = LocalRunSchedulerRepository(database).claim_next()
    writer = ModelTraceWriter(
        RunPaths.under(var_dir, UUID(run["run_id"])),
        run_id=UUID(run["run_id"]),
        attempt_id=UUID(claimed.attempt_id),
        attempt_no=1,
        capture_payloads=False,
    )
    assert not writer.path.exists()
    relative = writer.path.relative_to(var_dir).as_posix()
    projector = ModelTraceProjector(database, var_dir=var_dir)

    assert projector.project(
        run_id=run["run_id"], attempt_id=claimed.attempt_id, relative_path=relative
    ) == 0
    assert projector.project(
        run_id=run["run_id"], attempt_id=claimed.attempt_id, relative_path=relative
    ) == 0
