"""Regressions for current shared kernel and Studio surfaces."""

from __future__ import annotations
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import make_blank_definition
from generative_agents.modules import memory as memory_module
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.modules.game import Game
from generative_agents.modules.memory.action import Action
from generative_agents.modules.memory.event import Event
from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.context import RunControl, RunPaths, SimulationClock
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
