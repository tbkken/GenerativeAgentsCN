"""Ephemeral fact-rich server used only by the fresh Browser ROL journey.

This is deliberately a test fixture rather than a demo data path.  It creates
normal immutable revisions, scheduler-owned attempts, verified frames and
SQLite projections through production services, then serves the production
application with worker supervision disabled.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psutil
import uvicorn

from generative_agents.config import ExperimentDefinition
from generative_agents.config.bootstrap import make_builtin_definition
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import RunAttempt
from generative_agents.runtime.artifact_builder import ArtifactBuilder
from generative_agents.runtime.artifact_scheduler import ArtifactSchedulerRepository
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
    ModelTraceWriter,
)
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    ConversationMessage,
    ConversationRecord,
    DomainEventRecord,
    MemoryDelta,
    MemoryDeltaKind,
    ScheduleRevisionRecord,
    StepResultBuilder,
)
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.sqlite_result_projector import SqliteResultProjector
from generative_agents.runtime.trace_projector import ModelTraceProjector
from generative_agents.services import ExperimentService
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.runs import RunService
from generative_agents.web import create_app


def _definition(key: str) -> ExperimentDefinition:
    """为本测试模块封装 ``_definition`` 辅助步骤，减少重复的场景搭建代码。"""
    value = make_builtin_definition(
        key=key,
        name="回放浏览器验收实验",
        goal="验证真实地图、角色移动、时间轴事实与跨运行隔离",
    )
    payload = value.model_dump(mode="json", exclude_none=False)
    payload["simulation"]["max_steps"] = 3
    payload["simulation"]["checkpoint_interval_steps"] = 1
    payload["models"]["chat"]["resolved_model"] = "Qwen/browser-qa-chat"
    payload["models"]["embedding"]["resolved_model"] = "browser-qa-embedding"
    payload["agents"] = payload["agents"][:2]
    return ExperimentDefinition.model_validate(payload)


def _publish(database, var_dir: Path):
    """为本测试模块封装 ``_publish`` 辅助步骤，减少重复的场景搭建代码。"""
    service = ExperimentService(database)
    definition = _definition("browser-replay-qa")
    experiment = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = service.get_draft(experiment["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment["experiment_key"]
    draft = service.update_draft(
        experiment_id=experiment["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    revision = service.publish_draft(
        experiment_id=experiment["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    return experiment, revision, ExperimentDefinition.model_validate(payload)


def _result(run_id: str, attempt_id: str, definition: ExperimentDefinition, step_no: int):
    """为本测试模块封装 ``_result`` 辅助步骤，减少重复的场景搭建代码。"""
    first, second = definition.agents[:2]
    base_time = definition.simulation.start_time
    builder = StepResultBuilder(
        run_id=UUID(run_id),
        attempt_id=UUID(attempt_id),
        step_no=step_no,
        virtual_time=base_time + timedelta(minutes=definition.simulation.stride_minutes * step_no),
    )
    first_from = first.coord if step_no == 1 else (first.coord[0] + step_no - 1, first.coord[1])
    first_to = (first.coord[0] + step_no, first.coord[1])
    builder.add_agent(
        AgentStepResult(
            agent_key=first.agent_key,
            from_coord=first_from,
            to_coord=first_to,
            path=(first_from, first_to),
            action=ActionSnapshot(
                description=f"沿街前往咖啡馆（第 {step_no} 步）",
                emoji="☕",
                object_description="咖啡馆",
            ),
            activity_kind=ActivityKind.MOVING,
            location=("the Ville", "Hobbs Cafe"),
            currently="准备和邻居讨论今天的安排",
            schedule_item_id="morning-coffee",
        )
    )
    builder.add_agent(
        AgentStepResult(
            agent_key=second.agent_key,
            from_coord=second.coord,
            to_coord=second.coord,
            path=(second.coord,),
            action=ActionSnapshot(description="在咖啡馆整理笔记", emoji="📝"),
            activity_kind=ActivityKind.REST,
            location=("the Ville", "Hobbs Cafe"),
            currently="等待朋友到来",
            schedule_item_id="review-notes",
        )
    )
    if step_no == 2:
        builder.add_conversation(
            ConversationRecord(
                conversation_id=uuid4(),
                participant_agent_keys=(first.agent_key, second.agent_key),
                location=("the Ville", "Hobbs Cafe"),
                messages=(
                    ConversationMessage(
                        message_id=uuid4(),
                        sequence=1,
                        speaker_agent_key=first.agent_key,
                        content="我们下午一起去公园看看吧。",
                    ),
                ),
                summary="两位居民约定下午去公园",
                ended_reason="COMPLETE",
                duration_minutes=5,
                duration_source="OBSERVED",
            )
        )
        builder.add_memory_delta(
            MemoryDelta(
                event_id=uuid4(),
                sequence=1,
                agent_key=first.agent_key,
                memory_id="browser-memory-1",
                kind=MemoryDeltaKind.CREATED,
                memory_type="OBSERVATION",
                description="记住了下午去公园的约定",
                poignancy=6.0,
            )
        )
        builder.add_schedule_revision(
            ScheduleRevisionRecord(
                revision_id=uuid4(),
                sequence=1,
                agent_key=first.agent_key,
                reason="根据咖啡馆对话调整下午安排",
                source_event_id=None,
                content_hash="b" * 64,
                schedule=(
                    {
                        "item_id": "park-visit",
                        "start_minute": 840,
                        "duration_minutes": 60,
                        "description": "和邻居去公园",
                    },
                ),
            )
        )
        builder.add_domain_event(
            DomainEventRecord(
                event_id=uuid4(),
                sequence=1,
                event_type="social_plan_created",
                agent_keys=(first.agent_key, second.agent_key),
                payload={
                    "title": "形成共同出行计划",
                    "detail": "两位居民约定下午前往公园",
                    "location": "Hobbs Cafe",
                    "importance_score": 7,
                },
            )
        )
    return builder.freeze()


def _create_run(database, var_dir: Path, experiment_id: str, revision_id: str, definition, steps: int):
    """为本测试模块封装 ``_create_run`` 辅助步骤，减少重复的场景搭建代码。"""
    run = RunService(database, var_dir=var_dir).create_from_published(experiment_id, revision_id)
    scheduler = LocalRunSchedulerRepository(database, max_concurrent_runs=2)
    claimed = scheduler.claim_next()
    assert claimed is not None and claimed.run_id == run["run_id"]
    process = psutil.Process(os.getpid())
    assert scheduler.register_worker(
        claimed,
        pid=process.pid,
        pid_create_time=process.create_time(),
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    paths.ensure()
    projector = SqliteResultProjector(database, var_dir=var_dir)
    for step_no in range(1, steps + 1):
        result = _result(run["run_id"], claimed.attempt_id, definition, step_no)
        frame = FrameStore(paths).write(result)
        projector.commit_step(
            result,
            frame=frame,
            checkpoint_path=(paths.checkpoints / f"step-{step_no:06d}") if step_no == 2 else None,
        )
    return run, claimed, scheduler


def _seed(root: Path) -> dict:
    """为本测试模块封装 ``_seed`` 辅助步骤，减少重复的场景搭建代码。"""
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "browser-observability.db"
    database_url = "sqlite:///" + database_path.as_posix()
    upgrade_database(database_url)
    database = create_database(database_url)
    try:
        experiment, revision, definition = _publish(database, root)
        completed, completed_attempt, scheduler = _create_run(
            database, root, experiment["id"], revision["id"], definition, 3
        )
        assert scheduler.finish_worker(
            completed["run_id"], completed_attempt.attempt_id, exit_code=0
        )
        running, running_attempt, _ = _create_run(
            database, root, experiment["id"], revision["id"], definition, 2
        )
        metadata = {
            "database_url": database_url,
            "var_dir": str(root),
            "experiment_id": experiment["id"],
            "completed_run_id": completed["run_id"],
            "running_run_id": running["run_id"],
            "running_attempt_id": running_attempt.attempt_id,
            "agent_keys": [agent.agent_key for agent in definition.agents[:2]],
        }
        (root / "browser-fixture.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata
    finally:
        database.close()


def _augment_existing(root: Path) -> dict:
    """Add auditable observability facts without restarting the Browser server."""

    metadata_path = root / "browser-fixture.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    database = create_database(metadata["database_url"])
    try:
        run_id = metadata["running_run_id"]
        attempt_id = metadata["running_attempt_id"]
        paths = RunPaths.under(root, UUID(run_id))
        paths.ensure()

        with database.session_factory() as session:
            attempt = session.get(RunAttempt, attempt_id)
            assert attempt is not None and attempt.run_id == run_id
            attempt_log_path = root / attempt.log_path
        attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_log_path.write_text(
            "[2026-08-09 01:00:00] INFO 启动回放验收运行\n"
            "[2026-08-09 01:00:01] INFO Step 1 已提交：两位居民进入咖啡馆\n"
            "[2026-08-09 01:00:02] INFO Step 2 已提交：生成对话、记忆与日程修订\n",
            encoding="utf-8",
        )

        frame_store = FrameStore(paths)
        result = __import__(
            "generative_agents.runtime.results", fromlist=["StepResult"]
        ).StepResult.from_dict(frame_store.read_document(2)["result"])
        frame = frame_store.write(result)

        def export_storage(destination: Path) -> None:
            """为本测试模块封装 ``export_storage`` 辅助步骤，减少重复的场景搭建代码。"""
            (destination / "docstore.json").write_text(
                json.dumps(
                    {
                        "memory-browser-qa": {
                            "description": "记住了下午去公园的约定",
                            "type": "OBSERVATION",
                            "poignancy": 6,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (destination / "index_store.json").write_text(
                json.dumps({"nodes": ["memory-browser-qa"]}, ensure_ascii=False),
                encoding="utf-8",
            )

        checkpoint = CheckpointBundleWriter(
            paths,
            lambda current: CheckpointSnapshot(
                state={
                    "virtual_time": current.virtual_time.isoformat(),
                    "rng_state": [3, [42, 7, 2026], None],
                    "agents": {
                        metadata["agent_keys"][0]: {
                            "coord": list(current.agents[0].to_coord),
                            "currently": current.agents[0].currently,
                            "action": {
                                "description": current.agents[0].action.description,
                                "emoji": current.agents[0].action.emoji,
                                "address": list(current.agents[0].location),
                            },
                            "schedule": [
                                {
                                    "item_id": "park-visit",
                                    "start_minute": 840,
                                    "duration_minutes": 60,
                                }
                            ],
                        }
                    },
                },
                conversation={
                    "items": [
                        {
                            "participants": metadata["agent_keys"],
                            "messages": [
                                {
                                    "speaker": metadata["agent_keys"][0],
                                    "content": "我们下午一起去公园看看吧。",
                                }
                            ],
                        }
                    ]
                },
                storage_exporters={metadata["agent_keys"][0]: export_storage},
            ),
            retention=2,
        ).write(result, frame)

        trace_writer = ModelTraceWriter(
            paths,
            run_id=UUID(run_id),
            attempt_id=UUID(attempt_id),
            attempt_no=1,
            capture_payloads=True,
        )
        now = datetime.now(timezone.utc)
        call_id = uuid4()
        physical = ModelTraceEvent(
            event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
            run_id=UUID(run_id),
            attempt_id=UUID(attempt_id),
            call_id=call_id,
            step_no=2,
            agent_key=metadata["agent_keys"][0],
            purpose="plan",
            prompt_key="generate_plan",
            provider="vllm",
            resolved_model="Qwen/browser-qa-chat",
            started_at=now - timedelta(milliseconds=420),
            ended_at=now,
            latency_ms=420,
            attempt_no=1,
            status=ModelTraceStatus.SUCCEEDED,
            prompt_tokens=128,
            completion_tokens=36,
            total_tokens=164,
            payload={
                "request": "为居民规划下午活动",
                "response": "14:00 与邻居前往公园",
            },
        )
        logical = ModelTraceEvent(
            event_type=ModelTraceEventType.LOGICAL_END,
            run_id=UUID(run_id),
            attempt_id=UUID(attempt_id),
            call_id=call_id,
            step_no=2,
            agent_key=metadata["agent_keys"][0],
            purpose="plan",
            prompt_key="generate_plan",
            provider="vllm",
            resolved_model="Qwen/browser-qa-chat",
            started_at=now - timedelta(milliseconds=420),
            ended_at=now,
            latency_ms=420,
            attempt_no=None,
            status=ModelTraceStatus.SUCCEEDED,
            prompt_tokens=128,
            completion_tokens=36,
            total_tokens=164,
            payload={"outcome": "计划已提交到 Step 2"},
        )
        trace_writer.append(physical)
        trace_writer.append(logical)
        trace_relative = trace_writer.path.relative_to(root).as_posix()
        ModelTraceProjector(database, var_dir=root).project(
            run_id=run_id,
            attempt_id=attempt_id,
            relative_path=trace_relative,
        )

        artifact_repository = ArtifactSchedulerRepository(database)
        artifact_ids: dict[str, str] = {}
        artifact_job_ids: dict[str, str] = {}
        while True:
            claimed = artifact_repository.claim_next()
            if claimed is None:
                break
            job_log = root / claimed.log_path
            job_log.parent.mkdir(parents=True, exist_ok=True)
            job_log.write_text(
                f"INFO 开始构建 {claimed.job_id}\nINFO 校验 Run/Revision/source_step\n",
                encoding="utf-8",
            )
            artifact_id = ArtifactBuilder(database, var_dir=root).build(claimed.job_id)
            job_log.write_text(
                job_log.read_text(encoding="utf-8")
                + f"INFO 制品构建完成 artifact_id={artifact_id}\n",
                encoding="utf-8",
            )
            job = ArtifactService(database, var_dir=root).get_job(claimed.job_id)
            artifact_ids[job["job_type"]] = artifact_id
            artifact_job_ids[job["job_type"]] = claimed.job_id

        metadata.update(
            {
                "attempt_log_path": attempt.log_path,
                "model_trace_path": trace_relative,
                "checkpoint_step": 2,
                "checkpoint_path": checkpoint.relative_to(root).as_posix(),
                "artifact_ids": artifact_ids,
                "artifact_job_ids": artifact_job_ids,
            }
        )
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata
    finally:
        database.close()


def main() -> None:
    """为本测试模块封装 ``main`` 辅助步骤，减少重复的场景搭建代码。"""
    root = Path(os.environ["GA_BROWSER_QA_ROOT"]).resolve()
    port = int(os.environ.get("GA_BROWSER_QA_PORT", "8765"))
    metadata_path = root / "browser-fixture.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if os.environ.get("GA_BROWSER_QA_REUSE") == "1" and metadata_path.is_file()
        else _seed(root)
    )
    app = create_app(
        database_url=metadata["database_url"],
        var_dir=metadata["var_dir"],
        migrate=False,
        supervisor_enabled=False,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
