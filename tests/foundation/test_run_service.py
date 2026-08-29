"""基础能力回归测试：覆盖 ``test_run_service`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

import pytest

from generative_agents.config import ExperimentDefinition, validate_for_publish
from generative_agents.persistence.models import (
    Experiment,
    Run,
    RunAgentStep,
    RunAttempt,
    RunDomainEvent,
    RunEvent,
    RunQueue,
    RunStep,
)
from generative_agents.services.errors import ServiceError
from generative_agents.services.runs import RunService
from generative_agents.services.quality import RunQualityService
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.supervisor import LocalProcessSupervisor
from generative_agents.runtime.stall import RunStallDetector
from tests.support import brain_selection_for_database, publish_user_map


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


def _append_stationary_actions(
    database,
    *,
    run_id: str,
    attempt_id: str,
    count: int,
    event_type: str,
    predicate: str,
    object_value: str,
    vary_description: bool = False,
    expected_until_step: int | None = None,
):
    started = datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc)
    with database.session_factory.begin() as session:
        run = session.get(Run, run_id)
        run.completed_steps = count
        for step_no in range(1, count + 1):
            virtual_time = started + timedelta(minutes=step_no * 10)
            session.add(
                RunStep(
                    run_id=run_id,
                    step_no=step_no,
                    attempt_id=attempt_id,
                    virtual_time=virtual_time,
                    frame_path=f"frames/{step_no}.json",
                    frame_sha256="a" * 64,
                    action_count=1,
                    movement_count=0,
                    conversation_count=0,
                    message_count=0,
                    memory_created_count=0,
                    memory_accessed_count=0,
                    model_logical_calls=1,
                    model_retry_count=0,
                    active_agent_count=1,
                    checkpoint=False,
                )
            )
            # RunAgentStep has a composite FK to the committed step. Flush the
            # parent explicitly because these test rows have no ORM relationship.
            session.flush()
            description = f"Test Agent{predicate}{object_value}"
            if vary_description:
                description = f"{description}（计划等待第 {step_no} 步）"
            session.add(
                RunAgentStep(
                    run_id=run_id,
                    step_no=step_no,
                    agent_key="test-agent",
                    virtual_time=virtual_time,
                    x=0,
                    y=0,
                    address="test / home / bedroom / bed",
                    action_text=description,
                    action_emoji=None,
                    activity_kind="OTHER",
                    currently_text=description,
                    schedule_item_id=None,
                    path_source="OBSERVED",
                    decision_context_json={},
                )
            )
            session.add(
                RunDomainEvent(
                    id=str(uuid4()),
                    run_id=run_id,
                    step_no=step_no,
                    virtual_time=virtual_time,
                    event_type=event_type,
                    primary_agent_key="test-agent",
                    title=description,
                    detail=f"Test Agent / {predicate} / {object_value}",
                    location="test:home:bedroom:bed",
                    importance_score=0,
                    payload_json={
                        "subject": "Test Agent",
                        "predicate": predicate,
                        "object": object_value,
                        "structured_payload": {
                            "action_type": event_type,
                            "arguments": (
                                {"expected_until_step": expected_until_step}
                                if expected_until_step is not None
                                else {}
                            ),
                        },
                    },
                )
            )


def test_published_revision_creates_uuid_scoped_fifo_run(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_published_revision_creates_uuid_scoped_fifo_run`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")

    created = runs.create_from_published(experiment["id"], revision["id"])

    assert created["revision_id"] == revision["id"]
    assert created["status"] == "QUEUED"
    assert created["queue_position"] == 1
    with database.session_factory() as session:
        row = session.get(Run, created["run_id"])
        assert row.run_dir == f"runs/{created['run_id']}"
        assert session.scalar(select(RunQueue.run_id)) == created["run_id"]
        assert session.get(Experiment, experiment["id"]).latest_run_id == created["run_id"]

    with pytest.raises(ServiceError) as exc:
        runs.create_from_published(experiment["id"], revision["id"])
    assert exc.value.code == "EXPERIMENT_RUN_ACTIVE"


def test_run_creation_revalidates_legacy_published_revision(
    service, database, publishable_definition, tmp_path, monkeypatch
):
    """回归验证 ``test_run_creation_revalidates_legacy_published_revision`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    legacy_payload = publishable_definition.model_dump(mode="json", exclude_none=False)
    legacy_payload["agents"][0]["spatial"] = {"address": {}, "tree": {}}
    legacy_report = validate_for_publish(
        ExperimentDefinition.model_validate(legacy_payload)
    )
    monkeypatch.setattr(
        "generative_agents.services.runs.validate_for_publish",
        lambda _definition, **_kwargs: legacy_report,
    )

    runs = RunService(database, var_dir=tmp_path / "var")
    with pytest.raises(ServiceError) as caught:
        runs.create_from_published(experiment["id"], revision["id"])

    assert caught.value.code == "AGENT_SPATIAL_ADDRESS_REQUIRED"
    assert "当前运行要求" in caught.value.message
    with database.session_factory() as session:
        assert session.scalar(select(Run.id)) is None


def test_run_instants_keep_explicit_utc_offset_after_sqlite_round_trip(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_run_instants_keep_explicit_utc_offset_after_sqlite_round_trip`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])
    with database.session_factory.begin() as session:
        row = session.get(Run, created["run_id"])
        row.started_at = datetime(2026, 8, 9, 5, 20, 11, tzinfo=timezone.utc)
        row.finished_at = datetime(2026, 8, 9, 6, 9, 27, tzinfo=timezone.utc)

    serialized = runs.get_run(created["run_id"])

    assert serialized["created_at"].endswith("Z")
    assert serialized["started_at"] == "2026-08-09T05:20:11Z"
    assert serialized["finished_at"] == "2026-08-09T06:09:27Z"


def test_paused_run_cancels_without_new_attempt_or_slot(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_paused_run_cancels_without_new_attempt_or_slot`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    service_under_test = RunService(database, var_dir=tmp_path / "var")
    created = service_under_test.create_from_published(experiment["id"], revision["id"])
    with database.session_factory.begin() as session:
        run = session.get(Run, created["run_id"])
        session.query(RunQueue).filter(RunQueue.run_id == run.id).delete()
        run.status = "PAUSED"
        run.queued_at = None

    cancelled = service_under_test.cancel(created["run_id"])

    assert cancelled["status"] == "CANCELLED"
    assert cancelled["slot_no"] is None
    with database.session_factory() as session:
        assert session.scalar(
            select(RunAttempt).where(RunAttempt.run_id == created["run_id"])
        ) is None


def test_supervisor_requests_safe_pause_after_repeated_wait_facts(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    created = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert claimed is not None
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    _append_stationary_actions(
        database,
        run_id=created["run_id"],
        attempt_id=claimed.attempt_id,
        count=3,
        event_type="AGENT_WAITED",
        predicate="等待",
        object_value="下一轮",
    )

    report = RunStallDetector(
        database,
        wait_window_steps=3,
        repeated_action_window_steps=5,
    ).inspect()

    assert report.pause_requested_run_ids == (created["run_id"],)
    with database.session_factory() as session:
        run = session.get(Run, created["run_id"])
        assert run.status == "PAUSE_REQUESTED"
        detected = session.scalar(
            select(RunEvent)
            .where(
                RunEvent.run_id == created["run_id"],
                RunEvent.event_type == "stall_detected",
            )
            .order_by(RunEvent.id.desc())
        )
        assert detected.payload_json["reason"] == "REPEATED_WAIT_AT_SAME_LOCATION"
        assert detected.payload_json["repeat_count"] == 3
        assert detected.payload_json["event"] == {
            "subject": "Test Agent",
            "predicate": "等待",
            "object": "下一轮",
        }


def test_supervisor_does_not_pause_intentional_varying_wait(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    created = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert claimed is not None
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    _append_stationary_actions(
        database,
        run_id=created["run_id"],
        attempt_id=claimed.attempt_id,
        count=6,
        event_type="AGENT_WAITED",
        predicate="等待",
        object_value="私聊结束",
        vary_description=True,
    )

    report = RunStallDetector(
        database,
        wait_window_steps=6,
        repeated_action_window_steps=12,
    ).inspect()

    assert report.pause_requested_run_ids == ()
    with database.session_factory() as session:
        assert session.get(Run, created["run_id"]).status == "RUNNING"


def test_supervisor_does_not_reuse_previous_attempt_evidence(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    created = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert claimed is not None
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    _append_stationary_actions(
        database,
        run_id=created["run_id"],
        attempt_id=claimed.attempt_id,
        count=3,
        event_type="AGENT_WAITED",
        predicate="等待",
        object_value="下一轮",
    )
    # Model the instant after resume: the new Attempt starts at step 4 but has
    # not committed a step yet. Historical rows must not trigger a zero-progress
    # pause.
    with database.session_factory.begin() as session:
        session.get(RunAttempt, claimed.attempt_id).start_step = 4

    report = RunStallDetector(
        database,
        wait_window_steps=3,
        repeated_action_window_steps=5,
    ).inspect()

    assert report.pause_requested_run_ids == ()
    with database.session_factory() as session:
        assert session.get(Run, created["run_id"]).status == "RUNNING"


def test_repeated_act_is_diagnosed_without_unsafe_auto_pause(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    created = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert claimed is not None
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    _append_stationary_actions(
        database,
        run_id=created["run_id"],
        attempt_id=claimed.attempt_id,
        count=5,
        event_type="AGENT_ACTED",
        predicate="办公",
        object_value="处理项目文档",
    )

    detector = RunStallDetector(
        database,
        wait_window_steps=3,
        repeated_action_window_steps=5,
    )
    first = detector.inspect()
    second = detector.inspect()

    assert first.pause_requested_run_ids == ()
    assert first.suspected_run_ids == (created["run_id"],)
    assert second.suspected_run_ids == ()
    with database.session_factory() as session:
        assert session.get(Run, created["run_id"]).status == "RUNNING"
        suspected = list(
            session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == created["run_id"],
                    RunEvent.event_type == "stall_suspected",
                )
            )
        )
        assert len(suspected) == 1
        assert suspected[0].payload_json["reason"] == "REPEATED_STATIONARY_ACTION"


def test_completed_run_exposes_quality_post_processing_as_independent_pending_phase(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])
    with database.session_factory.begin() as session:
        run = session.get(Run, created["run_id"])
        session.query(RunQueue).filter(RunQueue.run_id == run.id).delete()
        run.status = "COMPLETED"
        run.completed_steps = run.requested_steps
        run.finished_at = datetime.now(timezone.utc)
        session.add(
            RunEvent(
                run_id=run.id,
                event_type="post_processing",
                payload_json={
                    "status": "RUNNING",
                    "phase": "QUALITY_EVALUATION",
                    "message": "仿真执行已完成，正在生成质量报告",
                },
            )
        )

    detail = runs.get_run(created["run_id"])
    quality = RunQualityService(database, var_dir=tmp_path / "var").get(
        created["run_id"]
    )

    assert detail["status"] == "COMPLETED"
    assert detail["post_processing"]["status"] == "RUNNING"
    assert quality["quality_status"] == "PENDING"
    assert quality["evaluator"]["status"] == "PENDING"


def test_reconcile_finishes_force_cancel_without_promoting_recovery_boundary(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_reconcile_finishes_force_cancel_without_promoting_recovery_boundary`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])
    scheduler = LocalRunSchedulerRepository(
        database, process_identity_matches=lambda _pid, _created: False
    )
    claimed = scheduler.claim_next()
    assert claimed is not None
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    with database.session_factory.begin() as session:
        run = session.get(Run, created["run_id"])
        run.status = "CANCEL_REQUESTED"
        run.completed_steps = 4
        run.recoverable_step = 2

    report = scheduler.reconcile()

    assert report.interrupted_run_ids == ()
    with database.session_factory() as session:
        run = session.get(Run, created["run_id"])
        assert run.status == "CANCELLED"
        assert run.completed_steps == 4
        assert run.recoverable_step == 2
        assert run.slot_no is None
        assert run.current_attempt_id is None


def test_terminal_run_can_be_archived_restored_and_deleted(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])

    with pytest.raises(ServiceError) as active_error:
        runs.set_archived(created["run_id"], archived=True)
    assert active_error.value.code == "RUN_ACTIVE"

    runs.cancel(created["run_id"])
    archived = runs.set_archived(created["run_id"], archived=True)
    active_page = runs.list_runs(experiment["id"], archived="active")
    archived_page = runs.list_runs(experiment["id"], archived="archived")
    restored = runs.set_archived(created["run_id"], archived=False)
    deleted = runs.delete_run(created["run_id"])

    assert archived["archived_at"] is not None
    assert active_page["items"] == []
    assert [item["run_id"] for item in archived_page["items"]] == [created["run_id"]]
    assert restored["archived_at"] is None
    assert deleted["deleted"] is True
    with pytest.raises(ServiceError) as missing:
        runs.get_run(created["run_id"])
    assert missing.value.code == "RUN_NOT_FOUND"


def test_run_history_uses_stable_cursor_and_can_reach_all_pages(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_run_history_uses_stable_cursor_and_can_reach_all_pages`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    service_under_test = RunService(database, var_dir=tmp_path / "var")
    created_ids = []
    for _ in range(5):
        run = service_under_test.create_from_published(experiment["id"], revision["id"])
        created_ids.append(run["run_id"])
        service_under_test.cancel(run["run_id"])

    first = service_under_test.list_runs(experiment["id"], limit=2)
    second = service_under_test.list_runs(
        experiment["id"], cursor=first["next_cursor"], limit=2
    )
    third = service_under_test.list_runs(
        experiment["id"], cursor=second["next_cursor"], limit=2
    )

    observed = [item["run_id"] for page in (first, second, third) for item in page["items"]]
    assert len(observed) == 5
    assert set(observed) == set(created_ids)
    assert len(observed) == len(set(observed))


def test_scheduler_claims_fifo_into_unique_slots_and_reconciles_dead_worker(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_scheduler_claims_fifo_into_unique_slots_and_reconciles_dead_worker`` 所描述的业务结果、故障边界和隔离约束。"""
    run_service = RunService(database, var_dir=tmp_path / "var")
    queued = []
    for index in range(3):
        payload = publishable_definition.model_dump(mode="json", exclude_none=False)
        payload["experiment"]["name"] = f"Scheduler {index}"
        payload["experiment"]["key"] = f"scheduler-{index}"
        definition = ExperimentDefinition.model_validate(payload)
        experiment, revision = _publish(service, definition)
        queued.append(
            run_service.create_from_published(experiment["id"], revision["id"])[
                "run_id"
            ]
        )
    scheduler = LocalRunSchedulerRepository(
        database,
        max_concurrent_runs=2,
        process_identity_matches=lambda pid, created: False,
    )

    first = scheduler.claim_next()
    second = scheduler.claim_next()

    assert [first.run_id, second.run_id] == queued[:2]
    assert [first.slot_no, second.slot_no] == [1, 2]
    assert scheduler.claim_next() is None
    assert scheduler.register_worker(first, pid=1234, pid_create_time=1.0) is True

    report = scheduler.reconcile()

    assert report.interrupted_run_ids == (first.run_id,)
    third = scheduler.claim_next()
    assert third.run_id == queued[2]
    assert third.slot_no == 1


def test_stale_worker_registration_cannot_take_over_new_attempt(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_stale_worker_registration_cannot_take_over_new_attempt`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    run_service = RunService(database, var_dir=tmp_path / "var")
    run_service.create_from_published(experiment["id"], revision["id"])
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    stale = type(claimed)(
        run_id=claimed.run_id,
        experiment_id=claimed.experiment_id,
        revision_id=claimed.revision_id,
        attempt_id="00000000-0000-0000-0000-000000000000",
        attempt_no=claimed.attempt_no,
        slot_no=claimed.slot_no,
        start_step=claimed.start_step,
        log_path=claimed.log_path,
    )

    assert scheduler.register_worker(stale, pid=99, pid_create_time=1.0) is False
    with database.session_factory() as session:
        run = session.get(Run, claimed.run_id)
        assert run.status == "STARTING"
        assert run.current_attempt_id == claimed.attempt_id


def test_worker_finish_honors_pause_boundary_and_releases_slot(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_worker_finish_honors_pause_boundary_and_releases_slot`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=os.getpid(), pid_create_time=1.0)
    with database.session_factory.begin() as session:
        row = session.get(Run, created["run_id"])
        row.status = "PAUSE_REQUESTED"
        row.completed_steps = 3
        row.recoverable_step = 3

    assert scheduler.finish_worker(
        claimed.run_id, claimed.attempt_id, exit_code=0
    )
    with database.session_factory() as session:
        row = session.get(Run, created["run_id"])
        attempt = session.get(RunAttempt, claimed.attempt_id)
        assert row.status == "PAUSED"
        assert row.slot_no is None
        assert row.recoverable_step == 3
        assert attempt.stop_reason == "PAUSED"


def test_worker_finish_preserves_structured_runtime_error(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_worker_finish_preserves_structured_runtime_error`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    runs = RunService(database, var_dir=tmp_path / "var")
    created = runs.create_from_published(experiment["id"], revision["id"])
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=os.getpid(), pid_create_time=1.0)

    assert scheduler.finish_worker(
        claimed.run_id,
        claimed.attempt_id,
        exit_code=1,
        error_code="AGENT_SPATIAL_CONFIGURATION_INVALID",
        error_message="Agent missing sleeping address",
    )

    with database.session_factory() as session:
        row = session.get(Run, created["run_id"])
        attempt = session.get(RunAttempt, claimed.attempt_id)
        assert row.status == "FAILED"
        assert row.error_code == "AGENT_SPATIAL_CONFIGURATION_INVALID"
        assert row.error_message == "Agent missing sleeping address"
        assert attempt.error_code == row.error_code
        assert attempt.error_message == row.error_message


def test_supervisor_materializes_manifest_before_process_registration(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_supervisor_materializes_manifest_before_process_registration`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    created = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    commands = []

    class FakeProcess:
        """测试替身 ``FakeProcess``：记录调用并返回当前场景可控的结果。"""
        pid = os.getpid()

        def poll(self):
            """为本测试模块封装 ``poll`` 辅助步骤，减少重复的场景搭建代码。"""
            return None

        def kill(self):
            """为本测试模块封装 ``kill`` 辅助步骤，减少重复的场景搭建代码。"""
            return None

    def process_factory(command, **_kwargs):
        """为本测试模块封装 ``process_factory`` 辅助步骤，减少重复的场景搭建代码。"""
        commands.append(command)
        return FakeProcess()

    supervisor = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        max_concurrent_runs=1,
        process_factory=process_factory,
        code_build_id="test-build",
    )
    supervisor.tick()

    with database.session_factory() as session:
        row = session.get(Run, created["run_id"])
        assert row.status == "RUNNING"
        assert row.pid == os.getpid()
    assert (var_dir / "runs" / created["run_id"] / "manifest.json").is_file()
    assert commands[0][0]
    assert created["run_id"] in commands[0]
    supervisor.stop()


def test_supervisor_restart_reuses_manifest_for_resume_start_step(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_supervisor_restart_reuses_manifest_for_resume_start_step`` 所描述的业务结果、故障边界和隔离约束。"""
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    created = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    claimed = LocalRunSchedulerRepository(database, max_concurrent_runs=1).claim_next()
    assert claimed is not None

    first_service = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        max_concurrent_runs=1,
        code_build_id="service-before-restart",
    )
    first_service._materialize_manifest(claimed)
    manifest_path = var_dir / "runs" / created["run_id"] / "manifest.json"
    original = manifest_path.read_bytes()

    resumed = replace(
        claimed,
        attempt_id="resume-attempt-2",
        attempt_no=2,
        start_step=95,
        log_path=f"runs/{created['run_id']}/logs/attempt-002.console.log",
    )
    restarted_service = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        max_concurrent_runs=1,
        code_build_id="service-after-restart",
    )
    restarted_service._materialize_manifest(resumed)

    assert resumed.start_step == 95
    assert manifest_path.read_bytes() == original
