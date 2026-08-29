"""架构红线测试：覆盖 ``test_adversarial_failure_boundaries`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import json
import inspect
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from filelock import Timeout
from sqlalchemy import select

from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import make_blank_definition
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import Run, RunEvent
from generative_agents.modules import memory as memory_module
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.modules.game import Game
from generative_agents.modules.memory.action import Action
from generative_agents.modules.memory.event import Event
from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.context import RunControl, RunPaths, SimulationClock
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    MemoryDelta,
    MemoryDeltaKind,
    StepResultBuilder,
)
from tests.support import brain_selection_for_database, publish_user_map
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.sqlite_result_projector import SqliteResultProjector
from generative_agents.runtime.worker import _prepare_attempt_state
from generative_agents.runtime.supervisor import LocalProcessSupervisor
from generative_agents.services import ExperimentService
from generative_agents.services.results import ResultQueryService
from generative_agents.services.runs import RunService
from generative_agents.start import SimulationRunner


def _definition(key: str) -> ExperimentDefinition:
    """为本测试模块封装 ``_definition`` 辅助步骤，减少重复的场景搭建代码。"""
    definition = make_blank_definition(key=key, name=f"Experiment {key}")
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["models"]["chat"]["resolved_model"] = "Qwen/test-chat"
    payload["models"]["embedding"]["resolved_model"] = "test-embedding"
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [1, 1],
        "map": [[0]],
        "camera": [0, 0],
        "tile_address_keys": {},
        "tiles": [
            {
                "coord": [0, 0],
                "collision": False,
                "address": ["home", "bedroom", "bed"],
            }
        ],
    }
    payload["agents"] = [
        {
            "agent_key": "test-agent",
            "enabled": True,
            "name": "Test Agent",
            "portrait_asset": None,
            "coord": [0, 0],
            "currently": "testing",
            "scratch": {
                "age": 30,
                "innate": "careful",
                "learned": "tests systems",
                "lifestyle": "repeatable",
                "daily_plan": "",
            },
            "spatial": {
                "address": {
                    "living_area": ["test", "home", "bedroom"],
                    "sleeping": ["test", "home", "bedroom", "bed"],
                },
                "tree": {"test": {"home": {"bedroom": ["bed"]}}},
            },
        }
    ]
    return ExperimentDefinition.model_validate(payload)


@pytest.fixture
def database(tmp_path: Path):
    """为本测试模块封装 ``database`` 辅助步骤，减少重复的场景搭建代码。"""
    database_url = "sqlite:///" + (tmp_path / "adversarial.db").as_posix()
    upgrade_database(database_url)
    value = create_database(database_url)
    yield value
    value.close()


def _publish(service: ExperimentService, definition: ExperimentDefinition):
    """为本测试模块封装 ``_publish`` 辅助步骤，减少重复的场景搭建代码。"""
    map_revision = publish_user_map(service.database, world=definition.world)
    experiment = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
        map_revision_id=map_revision["id"],
        **brain_selection_for_database(service.database),
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
    return experiment, revision


def _queue_run(database, tmp_path: Path, key: str):
    """为本测试模块封装 ``_queue_run`` 辅助步骤，减少重复的场景搭建代码。"""
    experiment, revision = _publish(ExperimentService(database), _definition(key))
    runs = RunService(database, var_dir=tmp_path / "var")
    run = runs.create_from_published(experiment["id"], revision["id"])
    return runs, run


def _step(run_id: UUID, attempt_id: UUID, step_no: int, *, memory=None):
    """为本测试模块封装 ``_step`` 辅助步骤，减少重复的场景搭建代码。"""
    builder = StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=step_no,
        virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(minutes=step_no),
    )
    builder.add_agent(
        AgentStepResult(
            agent_key="test-agent",
            from_coord=(0, 0),
            to_coord=(0, 0),
            path=((0, 0),),
            action=ActionSnapshot("wait"),
            activity_kind=ActivityKind.REST,
            location=("test",),
        )
    )
    if memory is not None:
        builder.add_memory_delta(memory)
    return builder.freeze()


def _checkpoint_writer(paths: RunPaths, *, retention: int = 3):
    """为本测试模块封装 ``_checkpoint_writer`` 辅助步骤，减少重复的场景搭建代码。"""
    def snapshot(result):
        """为本测试模块封装 ``snapshot`` 辅助步骤，减少重复的场景搭建代码。"""
        rng = random.Random(100 + result.step_no)

        def export_storage(target: Path):
            """为本测试模块封装 ``export_storage`` 辅助步骤，减少重复的场景搭建代码。"""
            (target / "marker.txt").write_text(
                f"checkpoint-{result.step_no}", encoding="utf-8"
            )

        return CheckpointSnapshot(
            state={
                "virtual_time": result.virtual_time.isoformat(),
                "rng_state": rng.getstate(),
                "agents": {"test-agent": {"coord": [result.step_no, 0]}},
            },
            conversation={"step": result.step_no},
            storage_exporters={"test-agent": export_storage},
        )

    return CheckpointBundleWriter(paths, snapshot, retention=retention)


def test_force_cancel_request_can_escalate_after_soft_cancel(
    database, tmp_path: Path
):
    """DEF-024: force=True must remain durable even after a soft request."""

    runs, run = _queue_run(database, tmp_path, "cancel-escalation")
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(
        claimed, pid=os.getpid(), pid_create_time=1.0
    )

    assert runs.cancel(run["run_id"], force=False)["status"] == "CANCEL_REQUESTED"
    assert runs.cancel(run["run_id"], force=True)["status"] == "CANCEL_REQUESTED"

    with database.session_factory() as session:
        latest = session.scalar(
            select(RunEvent)
            .where(
                RunEvent.run_id == run["run_id"],
                RunEvent.event_type == "state",
            )
            .order_by(RunEvent.id.desc())
        )
        assert latest.payload_json["force"] is True
        assert latest.payload_json["supervisor_action_required"] is True


def test_cancel_defaults_to_immediate_supervisor_termination(
    database, tmp_path: Path
):
    """The normal cancel command must not wait for a long blocking model call."""

    runs, run = _queue_run(database, tmp_path, "cancel-immediate-default")
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)

    assert runs.cancel(run["run_id"])["status"] == "CANCEL_REQUESTED"

    with database.session_factory() as session:
        latest = session.scalar(
            select(RunEvent)
            .where(
                RunEvent.run_id == run["run_id"],
                RunEvent.event_type == "state",
            )
            .order_by(RunEvent.id.desc())
        )
        assert latest.payload_json == {
            "status": "CANCEL_REQUESTED",
            "force": True,
            "supervisor_action_required": True,
        }


def test_force_kill_does_not_promote_uncheckpointed_completed_step(
    database, tmp_path: Path
):
    """A committed result can be readable without becoming resumable."""

    _runs, run = _queue_run(database, tmp_path, "force-boundary")
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(
        claimed, pid=os.getpid(), pid_create_time=1.0
    )
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "CANCEL_REQUESTED"
        row.completed_steps = 4
        row.recoverable_step = 2

    assert scheduler.finish_worker(
        claimed.run_id, claimed.attempt_id, exit_code=-9
    )
    with database.session_factory() as session:
        row = session.get(Run, run["run_id"])
        assert row.status == "CANCELLED"
        assert row.completed_steps == 4
        assert row.recoverable_step == 2


def test_reconcile_interrupts_live_process_with_stale_heartbeat(
    database, tmp_path: Path
):
    """DEF-028: PID liveness cannot substitute for the required heartbeat."""

    fixed_now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _runs, run = _queue_run(database, tmp_path, "stale-heartbeat")
    scheduler = LocalRunSchedulerRepository(
        database,
        now=lambda: fixed_now,
        process_identity_matches=lambda _pid, _created: True,
    )
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=4242, pid_create_time=1.0)
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.heartbeat_at = fixed_now - timedelta(minutes=10)

    report = scheduler.reconcile()

    assert report.interrupted_run_ids == (run["run_id"],)
    with database.session_factory() as session:
        assert session.get(Run, run["run_id"]).status == "INTERRUPTED"


def test_supervisor_requires_exclusive_scheduler_leader_lock():
    """DEF-027: one var_dir must never have two scheduling leaders."""

    source = inspect.getsource(LocalProcessSupervisor)
    assert "scheduler.lock" in source
    assert "FileLock" in source


def test_second_supervisor_cannot_start_for_same_var_dir(database, tmp_path: Path):
    """回归验证 ``test_second_supervisor_cannot_start_for_same_var_dir`` 所描述的业务结果、故障边界和隔离约束。"""
    first = LocalProcessSupervisor(database, var_dir=tmp_path / "var")
    second = LocalProcessSupervisor(database, var_dir=tmp_path / "var")
    first.start()
    try:
        with pytest.raises(Timeout):
            second.start()
    finally:
        second.stop()
        first.stop()


def test_resume_storage_is_attempt_local_and_cannot_pollute_future_attempt(
    database, tmp_path: Path
):
    """A resumed attempt must copy, never mutate, checkpoint index storage."""

    runs, run = _queue_run(database, tmp_path, "fresh-storage")
    scheduler = LocalRunSchedulerRepository(database)
    first = scheduler.claim_next()
    assert scheduler.register_worker(first, pid=os.getpid(), pid_create_time=1.0)
    run_id = UUID(run["run_id"])
    paths = RunPaths.under(tmp_path / "var", run_id)
    result = _step(run_id, UUID(first.attempt_id), 1)
    frame = FrameStore(paths).write(result)
    checkpoint = _checkpoint_writer(paths).write(result, frame)
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "PAUSE_REQUESTED"
        row.completed_steps = 1
        row.recoverable_step = 1

    _, _, first_storage = _prepare_attempt_state(
        database,
        paths,
        run_id=run["run_id"],
        attempt_id=first.attempt_id,
        start_step=2,
        stride_minutes=1,
    )
    first_marker = first_storage / "test-agent" / "associate" / "marker.txt"
    first_marker.write_text("attempt-one-mutated", encoding="utf-8")
    source_marker = checkpoint / "storage" / "test-agent" / "associate" / "marker.txt"
    assert source_marker.read_text(encoding="utf-8") == "checkpoint-1"

    assert scheduler.finish_worker(first.run_id, first.attempt_id, exit_code=0)
    runs.resume_paused(run["run_id"])
    second = scheduler.claim_next()
    assert second.attempt_id != first.attempt_id
    _, _, second_storage = _prepare_attempt_state(
        database,
        paths,
        run_id=run["run_id"],
        attempt_id=second.attempt_id,
        start_step=2,
        stride_minutes=1,
    )
    second_marker = second_storage / "test-agent" / "associate" / "marker.txt"
    assert second_marker.read_text(encoding="utf-8") == "checkpoint-1"


def test_resumed_first_step_uses_exact_checkpoint_coord_for_multi_tile_address(
    monkeypatch, tmp_path: Path
):
    """An action address is not an identity: resume must retain observed coord."""

    class FakeAssociate:
        """测试替身 ``FakeAssociate``：记录调用并返回当前场景可控的结果。"""
        def __init__(self, path, *_args, **_kwargs):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            self.last_evicted = ()
            marker = Path(path) / "marker.txt"
            self.loaded_marker = marker.read_text(encoding="utf-8")

        def to_dict(self):
            """为本测试模块封装 ``to_dict`` 辅助步骤，减少重复的场景搭建代码。"""
            return {"memory": {"event": [], "thought": [], "chat": []}}

    class Logger:
        """为 ``Logger`` 相关场景组织共享测试状态、输入或断言。"""
        def info(self, *_args, **_kwargs):
            """为本测试模块封装 ``info`` 辅助步骤，减少重复的场景搭建代码。"""
            pass

        debug = info
        warning = info

    class PoisonChoice(random.Random):
        """为 ``PoisonChoice`` 相关场景组织共享测试状态、输入或断言。"""
        def choice(self, _sequence):  # pragma: no cover - called only on regression
            """为本测试模块封装 ``choice`` 辅助步骤，减少重复的场景搭建代码。"""
            raise AssertionError("resume re-selected a tile from the action address")

    class Committer:
        """为 ``Committer`` 相关场景组织共享测试状态、输入或断言。"""
        def __init__(self):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            self.results = []

        def commit(self, result, *, force_checkpoint):
            """为本测试模块封装 ``commit`` 辅助步骤，减少重复的场景搭建代码。"""
            self.results.append(result)

    monkeypatch.setattr(memory_module, "Associate", FakeAssociate)
    definition = _definition("multi-tile-resume")
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [1, 2],
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": [
            {
                "coord": [0, 0],
                "address": ["shared", "room", "object"],
                "collision": False,
            },
            {
                "coord": [1, 0],
                "address": ["shared", "room", "object"],
                "collision": False,
            },
        ],
    }
    definition = ExperimentDefinition.model_validate(payload)
    config = ConfigAdapter().game_config(definition)
    clock = SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    config["storage_root"] = str(tmp_path / "attempt-storage")
    associate_root = (
        Path(config["storage_root"]) / "test-agent" / "associate"
    )
    associate_root.mkdir(parents=True)
    (associate_root / "marker.txt").write_text("restored-index", encoding="utf-8")
    agent_config = config["agents"]["test-agent"]
    agent_config["coord"] = [1, 0]
    agent_config["path"] = []
    agent_config["action"] = Action(
        Event(
            "Test Agent",
            "is",
            "waiting",
            address=["test", "shared", "room", "object"],
        ),
        clock=clock,
    ).to_dict()
    run_id, attempt_id = uuid4(), uuid4()
    context = SimpleNamespace(
        run_id=run_id,
        attempt_id=attempt_id,
        clock=clock,
        random=PoisonChoice(7),
        paths=RunPaths.under(tmp_path, run_id),
        skills={},
        models=None,
        metadata={},
        logger=Logger(),
        control=RunControl(),
        algorithm=get_algorithm_profile("ga-cn-v1"),
    )
    game = Game(config, {}, context=context)
    # Avoid model initialization; the step only captures the restored boundary.
    game.reset_game = lambda: None
    game.agent_think = lambda _key, _status, **_kwargs: {
        "plan": {"path": []},
        "world_action": {
            "action_type": "WAIT",
            "arguments": {"action_type": "WAIT", "description": "resumed"},
            "path": [],
        },
        "info": {"currently": "resumed"},
        "events": (),
    }
    committer = Committer()
    runner = SimulationRunner(context, game, committer)

    runner.run(1, stride_minutes=1)

    assert game.get_agent("test-agent").coord == (1, 0)
    assert game.get_agent("test-agent").associate.loaded_marker == "restored-index"
    assert committer.results[0].agents[0].from_coord == (1, 0)
    snapshot = json.loads(json.dumps(game.snapshot_state()))
    expected_next_random = context.random.random()
    context.random.random()
    game.restore_runtime_state(snapshot)
    assert context.random.random() == expected_next_random


def test_resume_selects_checkpoint_at_database_recoverable_boundary(
    database, tmp_path: Path
):
    """DEF-025: an orphan newer bundle must not permanently block recovery."""

    _runs, run = _queue_run(database, tmp_path, "orphan-checkpoint")
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=os.getpid(), pid_create_time=1.0)
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    paths = RunPaths.under(tmp_path / "var", run_id)
    store = FrameStore(paths)
    writer = _checkpoint_writer(paths)
    for step_no in (1, 2):
        result = _step(run_id, attempt_id, step_no)
        writer.write(result, store.write(result))
    # Models the documented crash after filesystem checkpoint commit but before
    # the SQLite projection transaction for step 2.
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.completed_steps = 1
        row.recoverable_step = 1

    state, conversation, _ = _prepare_attempt_state(
        database,
        paths,
        run_id=run["run_id"],
        attempt_id=claimed.attempt_id,
        start_step=2,
        stride_minutes=1,
    )
    assert state["agents"]["test-agent"]["coord"] == [1, 0]
    assert conversation == {"step": 1}


def test_checkpoint_scan_rejects_semantically_tampered_bundle(database, tmp_path: Path):
    """DEF-026: scanning cannot trust a self-consistent but mislabelled bundle."""

    _runs, run = _queue_run(database, tmp_path, "bundle-semantics")
    claimed = LocalRunSchedulerRepository(database).claim_next()
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    paths = RunPaths.under(tmp_path / "var", run_id)
    store = FrameStore(paths)
    writer = _checkpoint_writer(paths)
    for step_no in (1, 2):
        result = _step(run_id, attempt_id, step_no)
        writer.write(result, store.write(result))

    newest_bundle = paths.checkpoints / "step-000002" / "bundle.json"
    document = json.loads(newest_bundle.read_text(encoding="utf-8"))
    document["step_no"] = 999
    newest_bundle.write_text(json.dumps(document), encoding="utf-8")
    (paths.checkpoints / "LATEST").write_text("not-json", encoding="utf-8")

    recovered = writer.read_latest()
    assert recovered is not None
    assert recovered.path.name == "step-000001"


def test_concurrent_claims_and_cross_run_eviction_facts_are_isolated(
    database, tmp_path: Path
):
    """Exercise SQLite contention and identical memory IDs in different Runs."""

    queued = []
    for index in range(5):
        _runs, run = _queue_run(database, tmp_path, f"concurrent-{index}")
        queued.append(run["run_id"])
    scheduler = LocalRunSchedulerRepository(database, max_concurrent_runs=3)
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: scheduler.claim_next(), range(5)))
    claimed = [item for item in claims if item is not None]
    assert {item.run_id for item in claimed} == set(queued[:3])
    assert {item.slot_no for item in claimed} == {1, 2, 3}

    var_dir = tmp_path / "var"
    projector = SqliteResultProjector(database, var_dir=var_dir)
    by_run_id = {item.run_id: item for item in claimed}
    first_run, second_run = queued[:2]
    for run_id_text, memory_kind, description in (
        (first_run, MemoryDeltaKind.CREATED, "run-a memory"),
        (second_run, MemoryDeltaKind.CREATED, "run-b memory"),
    ):
        claim = by_run_id[run_id_text]
        assert scheduler.register_worker(claim, pid=os.getpid(), pid_create_time=1.0)
        run_id = UUID(run_id_text)
        memory = MemoryDelta(
            event_id=uuid4(),
            sequence=1,
            agent_key="test-agent",
            memory_id="shared-memory-id",
            kind=memory_kind,
            memory_type="EVENT",
            description=description,
        )
        result = _step(run_id, UUID(claim.attempt_id), 1, memory=memory)
        frame = FrameStore(RunPaths.under(var_dir, run_id)).write(result)
        projector.commit_step(result, frame=frame, checkpoint_path=None)

    first_claim = by_run_id[first_run]
    evicted = MemoryDelta(
        event_id=uuid4(),
        sequence=1,
        agent_key="test-agent",
        memory_id="shared-memory-id",
        kind=MemoryDeltaKind.EVICTED,
        memory_type="EVENT",
    )
    result = _step(UUID(first_run), UUID(first_claim.attempt_id), 2, memory=evicted)
    frame = FrameStore(RunPaths.under(var_dir, UUID(first_run))).write(result)
    projector.commit_step(result, frame=frame, checkpoint_path=None)

    queries = ResultQueryService(database)
    first_items = queries.memories(first_run, state="EVICTED")["items"]
    second_items = queries.memories(second_run, state="ACTIVE")["items"]
    assert [(item["description"], item["removed_step"]) for item in first_items] == [
        ("run-a memory", 2)
    ]
    assert [item["description"] for item in second_items] == ["run-b memory"]
    assert queries.memories(first_run, query="run-b")["items"] == []
    assert queries.memories(second_run, query="run-a")["items"] == []


def test_run_detail_exposes_projected_available_step(database, tmp_path: Path):
    """DEF-029: run selector facts must use the result projection boundary."""

    runs, run = _queue_run(database, tmp_path, "available-step")
    scheduler = LocalRunSchedulerRepository(database)
    claimed = scheduler.claim_next()
    assert scheduler.register_worker(claimed, pid=os.getpid(), pid_create_time=1.0)
    run_id = UUID(run["run_id"])
    result = _step(run_id, UUID(claimed.attempt_id), 1)
    var_dir = tmp_path / "var"
    frame = FrameStore(RunPaths.under(var_dir, run_id)).write(result)
    SqliteResultProjector(database, var_dir=var_dir).commit_step(
        result, frame=frame, checkpoint_path=None
    )

    assert runs.get_run(run["run_id"])["available_step"] == 1
