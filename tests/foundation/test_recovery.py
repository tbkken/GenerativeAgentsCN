"""基础能力回归测试：覆盖 ``test_recovery`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import Run, RunResultSummary
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.results import StepResultBuilder
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.sqlite_result_projector import SqliteResultProjector
from generative_agents.services.runs import RunService
from tests.support import brain_selection_for_database, publish_user_map


def _run(service, database, definition: ExperimentDefinition, var_dir):
    """为本测试模块封装 ``_run`` 辅助步骤，减少重复的场景搭建代码。"""
    map_revision = publish_user_map(database, world=definition.world)
    experiment = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
        map_revision_id=map_revision["id"],
        **brain_selection_for_database(database),
    )
    draft = service.get_draft(experiment["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment["experiment_key"]
    payload["world"] = draft["definition"]["world"]
    payload["engine"] = draft["definition"]["engine"]
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
    return RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )


def test_interrupted_resume_rebuilds_views_at_checkpoint_and_quarantines_future_frame(
    service, database, publishable_definition, tmp_path
):
    """回归验证 ``test_interrupted_resume_rebuilds_views_at_checkpoint_and_quarantines_future_frame`` 所描述的业务结果、故障边界和隔离约束。"""
    var_dir = tmp_path / "var"
    created = _run(service, database, publishable_definition, var_dir)
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=123, pid_create_time=1.0)
    paths = RunPaths.under(var_dir, UUID(created["run_id"]))
    frames = FrameStore(paths)
    projector = SqliteResultProjector(database, var_dir=var_dir)
    checkpoint = CheckpointBundleWriter(
        paths,
        lambda result: CheckpointSnapshot(
            state={
                "agents": {"test-agent": {}},
                "virtual_time": result.virtual_time.isoformat(),
                "rng_state": [3, [1, 2, 3], None],
            },
            conversation={},
        ),
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = StepResultBuilder(
        UUID(created["run_id"]), UUID(claimed.attempt_id), 1, start
    ).freeze()
    first_frame = frames.write(first)
    first_checkpoint = checkpoint.write(first, first_frame)
    projector.commit_step(first, frame=first_frame, checkpoint_path=first_checkpoint)
    second = StepResultBuilder(
        UUID(created["run_id"]),
        UUID(claimed.attempt_id),
        2,
        start + timedelta(minutes=1),
    ).freeze()
    second_frame = frames.write(second)
    projector.commit_step(second, frame=second_frame, checkpoint_path=None)
    with database.session_factory.begin() as session:
        row = session.get(Run, created["run_id"])
        row.status = "INTERRUPTED"
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None

    resumed = RunService(database, var_dir=var_dir).resume_paused(created["run_id"])

    assert resumed["status"] == "QUEUED"
    assert resumed["available_step"] == 1
    assert resumed["completed_steps"] == resumed["recoverable_step"] == 1
    assert not second_frame.path.exists()
    assert list(paths.orphaned.rglob(second_frame.path.name))
    with database.session_factory() as session:
        assert session.get(RunResultSummary, created["run_id"]).available_step == 1
    retried = scheduler.claim_next()
    assert retried.start_step == 2
