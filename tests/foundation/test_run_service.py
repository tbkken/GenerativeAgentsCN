from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

from sqlalchemy import select

import pytest

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import Experiment, Run, RunAttempt, RunQueue
from generative_agents.services.errors import ServiceError
from generative_agents.services.runs import RunService
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.supervisor import LocalProcessSupervisor


def _publish(service, definition: ExperimentDefinition):
    created = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = service.get_draft(created["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = created["experiment_key"]
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


def test_published_revision_creates_uuid_scoped_fifo_run(
    service, database, publishable_definition, tmp_path
):
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


def test_paused_run_cancels_without_new_attempt_or_slot(
    service, database, publishable_definition, tmp_path
):
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


def test_reconcile_finishes_force_cancel_without_promoting_recovery_boundary(
    service, database, publishable_definition, tmp_path
):
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


def test_run_history_uses_stable_cursor_and_can_reach_all_pages(
    service, database, publishable_definition, tmp_path
):
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


def test_supervisor_materializes_manifest_before_process_registration(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _publish(service, publishable_definition)
    var_dir = tmp_path / "var"
    created = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    commands = []

    class FakeProcess:
        pid = os.getpid()

        def poll(self):
            return None

        def kill(self):
            return None

    def process_factory(command, **_kwargs):
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
