"""兼容性回归测试：覆盖 ``test_engine_isolation_regression`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import copy
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from generative_agents.compress import build_replay
from generative_agents.config import definition_hash
from generative_agents.config.schema import make_blank_definition
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.modules.game import Game
from generative_agents.modules.maze import Maze
from generative_agents.modules.memory.action import Action
from generative_agents.modules.memory.event import Event
from generative_agents.modules.model.llm_model import LLMModel
from generative_agents.runtime import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    FrameStore,
    ModelTraceWriter,
    MemoryDeltaKind,
    RunControl,
    RunPaths,
    SimulationClock,
    StepResult,
    StepResultBuilder,
    RunManifestStore,
    build_manifest_document,
)
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.runtime.replay_v2 import validate_replay_v2
from generative_agents.skills import SkillRegistry
from generative_agents.start import SimulationRunner, apply_checkpoint_state


class _Logger:
    """为 ``_Logger`` 相关场景组织共享测试状态、输入或断言。"""
    def info(self, *_args, **_kwargs):
        """为本测试模块封装 ``info`` 辅助步骤，减少重复的场景搭建代码。"""
        pass

    debug = info
    warning = info


class _Event:
    """为 ``_Event`` 相关场景组织共享测试状态、输入或断言。"""
    predicate = "moving"
    emoji = "🚶"

    def get_describe(self):
        """为本测试模块封装 ``get_describe`` 辅助步骤，减少重复的场景搭建代码。"""
        return "walks to the cafe"


class _Tile:
    """为 ``_Tile`` 相关场景组织共享测试状态、输入或断言。"""
    def get_address(self):
        """为本测试模块封装 ``get_address`` 辅助步骤，减少重复的场景搭建代码。"""
        return ["world", "cafe"]


class _FakeAgent:
    """测试替身 ``_FakeAgent``：记录调用并返回当前场景可控的结果。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.name = "Agent A"
        self.coord = (1, 1)
        self.path = []

    def get_event(self, as_act=True):
        """为本测试模块封装 ``get_event`` 辅助步骤，减少重复的场景搭建代码。"""
        return _Event() if as_act else None

    def get_tile(self):
        """为本测试模块封装 ``get_tile`` 辅助步骤，减少重复的场景搭建代码。"""
        return _Tile()


class _FakeGame:
    """测试替身 ``_FakeGame``：记录调用并返回当前场景可控的结果。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.agents = {"agent-a": _FakeAgent()}
        self.agent_keys_by_name = {"Agent A": "agent-a"}

    def reset_game(self):
        """为本测试模块封装 ``reset_game`` 辅助步骤，减少重复的场景搭建代码。"""
        pass

    def get_agent(self, key):
        """为本测试模块封装 ``get_agent`` 辅助步骤，减少重复的场景搭建代码。"""
        return self.agents[key]

    def agent_think(
        self,
        key,
        _status,
        *,
        step_no,
        total_steps,
        stride_minutes,
    ):
        """为本测试模块封装 ``agent_think`` 辅助步骤，减少重复的场景搭建代码。"""
        agent = self.agents[key]
        return {
            "plan": {"path": [(1, 1), (2, 1)]},
            "info": {"currently": "walking"},
            "events": (),
        }

    def commit_world_action(
        self,
        key,
        outcome,
        *,
        stride_minutes,
        movement_budget,
    ):
        agent = self.agents[key]
        from_coord = tuple(agent.coord)
        planned_path = ((1, 1), (2, 1))
        agent.coord = (2, 1)
        executed_path = planned_path
        outcome["events"] = (
            {
                "kind": "world_domain_event",
                "event_type": "AGENT_MOVED",
                "agent_keys": (key,),
                "subject": agent.name,
                "predicate": "moves to",
                "object": "world:cafe",
                "structured_payload": {
                    "action_type": "MOVE",
                    "from_coord": list(from_coord),
                    "to_coord": [2, 1],
                    "executed_path": [list(coord) for coord in executed_path],
                    "planned_path": [list(coord) for coord in planned_path],
                    "remaining_path": [],
                },
            },
        )
        return {
            "outcome": outcome,
            "planned_path": planned_path,
            "executed_path": executed_path,
            "remaining_path": (),
        }


class _Committer:
    """为 ``_Committer`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.items = []

    def commit(self, result, *, force_checkpoint):
        """为本测试模块封装 ``commit`` 辅助步骤，减少重复的场景搭建代码。"""
        self.items.append((result, force_checkpoint))


def test_simulation_runner_commits_complete_observed_step_result(tmp_path):
    """回归验证 ``test_simulation_runner_commits_complete_observed_step_result`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id, attempt_id = uuid4(), uuid4()
    context = SimpleNamespace(
        run_id=run_id,
        attempt_id=attempt_id,
        clock=SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        control=RunControl(),
    )
    committer = _Committer()
    runner = SimulationRunner(context, _FakeGame(), committer)
    assert runner.run(1, stride_minutes=10) == 1
    result, forced = committer.items[0]
    assert forced is True
    assert result.run_id == run_id and result.attempt_id == attempt_id
    assert result.agents[0].path == ((1, 1), (2, 1))
    assert result.agents[0].path_source == "OBSERVED"
    assert result.agents[0].currently == "walking"
    assert result.domain_events[0].event_type == "AGENT_MOVED"


def test_clock_and_rng_are_run_local_when_interleaved():
    """回归验证 ``test_clock_and_rng_are_run_local_when_interleaved`` 所描述的业务结果、故障边界和隔离约束。"""
    a_clock = SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    b_clock = SimulationClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
    a_rng, b_rng = random.Random(1), random.Random(2)
    a_clock.forward(10)
    b_clock.forward(20)
    assert a_clock.get_date("%Y") == "2026"
    assert b_clock.get_date("%Y") == "2030"
    assert [a_rng.random(), b_rng.random()] == [
        random.Random(1).random(),
        random.Random(2).random(),
    ]


def test_game_checkpoint_round_trip_restores_run_local_rng_state():
    """回归验证 ``test_game_checkpoint_round_trip_restores_run_local_rng_state`` 所描述的业务结果、故障边界和隔离约束。"""
    game = Game.__new__(Game)
    game.context = SimpleNamespace(random=random.Random(42))
    game.agents = {}
    game.game_object_interactions = SimpleNamespace(
        snapshot_state=lambda: {},
        restore_state=lambda state: None,
    )
    game.context.clock = SimulationClock(
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    snapshot = json.loads(json.dumps(game.snapshot_state()))
    expected = game.context.random.random()
    game.context.random.random()
    game.restore_runtime_state(snapshot)
    assert game.context.random.random() == expected


def test_checkpoint_state_overlay_is_isolated_and_requires_same_agents():
    """回归验证 ``test_checkpoint_state_overlay_is_isolated_and_requires_same_agents`` 所描述的业务结果、故障边界和隔离约束。"""
    config = {
        "agents": {
            "agent-a": {
                "coord": [1, 1],
                "associate": {"embedding": {"model": "embed"}, "memory": {}},
            }
        }
    }
    original = copy.deepcopy(config)
    restored = apply_checkpoint_state(
        config,
        {
            "agents": {
                "agent-a": {
                    "coord": [2, 3],
                    "associate": {"memory": {"event": ["node-1"]}},
                }
            }
        },
    )
    assert config == original
    assert restored["agents"]["agent-a"]["coord"] == [2, 3]
    assert restored["agents"]["agent-a"]["associate"] == {
        "embedding": {"model": "embed"},
        "memory": {"event": ["node-1"]},
    }
    try:
        apply_checkpoint_state(config, {"agents": {"agent-b": {}}})
    except ValueError as exc:
        assert "agent keys" in str(exc)
    else:
        raise AssertionError("mismatched checkpoint agents must be rejected")


def test_memory_eviction_is_preserved_as_a_result_delta():
    """回归验证 ``test_memory_eviction_is_preserved_as_a_result_delta`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id, attempt_id = uuid4(), uuid4()
    collector = StepResultCollector(
        StepResultBuilder(
            run_id=run_id,
            attempt_id=attempt_id,
            step_no=1,
            virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        name_to_key={},
    )
    collector._capture_event(
        {
            "kind": "memory",
            "memory_kind": "EVICTED",
            "agent_key": "agent-a",
            "memory_id": "node-1",
            "memory_type": "EVENT",
        }
    )
    result = collector.freeze()
    assert result.memory_deltas[0].kind is MemoryDeltaKind.EVICTED


def test_agent_decision_context_keeps_product_facts_without_full_memory_storage():
    """回归验证 ``test_agent_decision_context_keeps_product_facts_without_full_memory_storage`` 所描述的业务结果、故障边界和隔离约束。"""
    context = StepResultCollector._decision_context(
        {"path": [(1, 2), (2, 2)]},
        {
            "concepts": {
                f"node-{index}": {"event(P.5)": f"perception {index}"}
                for index in range(25)
            },
            "schedule": {"10:00~10:30": "prepare party"},
            "action": {"event": "collect decorations"},
            "associate": {
                "nodes": 120,
                "event": ["event"] * 8,
                "chat": ["chat"] * 4,
                "thought": ["thought"] * 3,
            },
        },
    )

    assert len(context["perceptions"]) == 20
    assert context["schedule"] == {"10:00~10:30": "prepare party"}
    assert context["action"]["event"] == "collect decorations"
    assert context["path"] == [[1, 2], [2, 2]]
    assert context["memory_counts"] == {"event": 8, "chat": 4, "thought": 3}
    assert "associate" not in context, "the full memory index must not be duplicated per step"


def test_maze_action_and_config_adapter_do_not_mutate_revision_inputs():
    """回归验证 ``test_maze_action_and_config_adapter_do_not_mutate_revision_inputs`` 所描述的业务结果、故障边界和隔离约束。"""
    publishable_definition = make_blank_definition(
        key="adapter-test", name="Adapter test"
    )
    world = {
        "world": "test",
        "size": [3, 3],
        "tile_size": 16,
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": [{"coord": [1, 1], "address": ["s", "a"], "collision": False}],
    }
    original_world = copy.deepcopy(world)
    Maze(world, _Logger(), random.Random(1))
    assert world == original_world

    raw_action = {
        "event": Event("a", "is", "idle", address=["world"]).to_dict(),
        "obj_event": None,
        "start": "20260101-00:00:00",
        "duration": 1,
    }
    original_action = copy.deepcopy(raw_action)
    Action.from_dict(
        raw_action,
        clock=SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    assert raw_action == original_action

    before = publishable_definition.model_dump(mode="json", exclude_none=False)
    ConfigAdapter().game_config(publishable_definition)
    assert publishable_definition.model_dump(mode="json", exclude_none=False) == before


class _RetryingLLM(LLMModel):
    """为 ``_RetryingLLM`` 相关场景组织共享测试状态、输入或断言。"""
    def setup(self, _config):
        """构造当前测试场景所需的 ``setup`` 数据、文件或受控对象。"""
        self.calls = 0
        return None

    def _completion(self, _prompt, _return_type, **_kwargs):
        """为本测试模块封装 ``_completion`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        self._last_usage = {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        }
        return "ok"


def test_llm_trace_records_each_physical_attempt_and_one_logical_end(tmp_path):
    """回归验证 ``test_llm_trace_records_each_physical_attempt_and_one_logical_end`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id, attempt_id = uuid4(), uuid4()
    writer = ModelTraceWriter(
        RunPaths.under(tmp_path, run_id),
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=1,
        capture_payloads=False,
    )
    model = _RetryingLLM(
        {
            "api_key": "secret-not-recorded",
            "base_url": "http://127.0.0.1",
            "model": "fake",
            "retry_attempts": 2,
            "retry_backoff_seconds": 0,
        },
        recorder=writer,
    )
    assert model.completion("prompt", caller="schedule", agent_key="agent-a") == "ok"
    records = [json.loads(line) for line in writer.path.read_text().splitlines()]
    assert [record["event_type"] for record in records] == [
        "PHYSICAL_START",
        "PHYSICAL_ATTEMPT",
        "PHYSICAL_START",
        "PHYSICAL_ATTEMPT",
        "LOGICAL_END",
    ]
    assert [record["status"] for record in records] == [
        "RUNNING",
        "FAILED",
        "RUNNING",
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert records[3]["total_tokens"] == 5
    assert "secret-not-recorded" not in writer.path.read_text()


def test_vector_indexes_receive_embedding_instances_without_global_settings(monkeypatch):
    """回归验证 ``test_vector_indexes_receive_embedding_instances_without_global_settings`` 所描述的业务结果、故障边界和隔离约束。"""
    from generative_agents.modules.storage import index as index_module

    created = []

    class FakeEmbedding:
        """测试替身 ``FakeEmbedding``：记录调用并返回当前场景可控的结果。"""
        def __init__(self, model_name):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            self.model_name = model_name

    class FakeIndex:
        """测试替身 ``FakeIndex``：记录调用并返回当前场景可控的结果。"""
        def __init__(self, nodes, **kwargs):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            created.append((nodes, kwargs))
            self.docstore = SimpleNamespace(docs={})

    monkeypatch.setattr(index_module, "HuggingFaceEmbedding", FakeEmbedding)
    monkeypatch.setattr(index_module.index_core, "VectorStoreIndex", FakeIndex)
    clock = SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    first = index_module.LlamaIndex(
        {"provider": "hugging_face", "model": "embed-a"}, clock=clock
    )
    second = index_module.LlamaIndex(
        {"provider": "hugging_face", "model": "embed-b"}, clock=clock
    )
    assert first._embed_model.model_name == "embed-a"
    assert second._embed_model.model_name == "embed-b"
    assert created[0][1]["embed_model"] is first._embed_model
    assert created[1][1]["embed_model"] is second._embed_model
    assert created[0][1]["transformations"] is first._transformations


def test_legacy_naive_checkpoint_index_dates_are_interpreted_as_simulation_utc():
    """回归验证 ``test_legacy_naive_checkpoint_index_dates_are_interpreted_as_simulation_utc`` 所描述的业务结果、故障边界和隔离约束。"""
    from generative_agents.modules.storage import index as index_module

    removed = []
    legacy_nodes = {
        "expired": SimpleNamespace(
            metadata={"create": "20251231-23:00:00", "expire": "20260101-00:30:00"}
        ),
        "retained": SimpleNamespace(
            metadata={"create": "20260101-00:45:00", "expire": "20260102-00:00:00"}
        ),
    }
    index = object.__new__(index_module.LlamaIndex)
    index._clock = SimulationClock(datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc))
    index._index = SimpleNamespace(docstore=SimpleNamespace(docs=legacy_nodes))
    index.remove_nodes = lambda node_ids, delete_from_docstore=True: removed.extend(node_ids)

    assert index.cleanup() == ["expired"]
    assert removed == ["expired"]
    assert index_module.utils.to_date("20260101-00:45:00").utcoffset() == timedelta(0)


def test_replay_artifact_uses_observed_frame_path(tmp_path):
    """回归验证 ``test_replay_artifact_uses_observed_frame_path`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id, attempt_id, experiment_id, revision_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    paths = RunPaths.under(tmp_path, run_id)
    definition = make_blank_definition(key="replay-test", name="Replay")
    document = build_manifest_document(
        run_id=run_id,
        experiment_id=experiment_id,
        revision_id=revision_id,
        definition=definition,
        expected_definition_hash=definition_hash(definition),
        code_build_id="test",
        assets=[],
        materialized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dependency_versions={},
        skill_bundle=SkillRegistry().snapshot(),
    )
    manifest = RunManifestStore(paths).materialize(document)
    result = StepResult(
            run_id=run_id,
            attempt_id=attempt_id,
            step_no=1,
            virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            agents=(
                AgentStepResult(
                    agent_key="agent-a",
                    from_coord=(1, 1),
                    to_coord=(2, 1),
                    path=((1, 1), (2, 1)),
                    action=ActionSnapshot("walk"),
                    activity_kind=ActivityKind.MOVING,
                    location=("world", "cafe"),
                    path_source="OBSERVED",
                ),
            ),
            conversations=(),
            memory_deltas=(),
            schedule_revisions=(),
            domain_events=(),
            committed_model_usage=(),
        )
    frame = FrameStore(paths).write(result)
    from generative_agents.runtime.file_result_projector import FileResultProjector

    FileResultProjector(paths).commit_step(
        result,
        frame=frame,
        checkpoint_path=None,
    )
    # A crash can leave a durable frame that never reached the authoritative
    # projection. Compression must not include that future/uncommitted fact.
    FrameStore(paths).write(
        StepResult(
            run_id=run_id,
            attempt_id=attempt_id,
            step_no=2,
            virtual_time=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
            agents=(),
            conversations=(),
            memory_deltas=(),
            schedule_revisions=(),
            domain_events=(),
            committed_model_usage=(),
        )
    )
    artifact = build_replay(paths, manifest)
    replay = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert validate_replay_v2(replay) == replay
    assert replay["schema_version"] == 2
    assert replay["source_kind"] == "RUN_FRAMES"
    assert replay["source_step"] == 1
    agent = replay["steps"][0]["agents"][0]
    assert agent["path_source"] == "OBSERVED"
    assert {tuple(sample) for sample in agent["path"]} == {(1, 1), (2, 1)}
