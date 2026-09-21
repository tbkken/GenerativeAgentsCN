"""Regressions for current shared kernel and Studio surfaces."""

from __future__ import annotations
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from generative_agents.ga_runtime.engine.objects import GameObjectInteractionSystem
from generative_agents.ga_runtime.memory.event import Event
from generative_agents.ga_runtime.engine.context import RunControl
from generative_agents.ga_runtime.engine.context import SimulationClock
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.engine.scheduler import SimulationRunner

def _world() -> dict:
    """为本测试模块封装 ``_world`` 辅助步骤，减少重复的场景搭建代码。"""
    return {
        "editor_v2": {
            "hierarchy_nodes": [
                {
                    "id": "world",
                    "kind": "WORLD",
                    "name": "过街演示",
                    "parent_id": None,
                    "bounds": {"x": 0, "y": 0, "width": 9, "height": 7},
                },
                {
                    "id": "road",
                    "kind": "SECTOR",
                    "name": "道路",
                    "parent_id": "world",
                    "bounds": {"x": 0, "y": 2, "width": 9, "height": 3},
                },
                {
                    "id": "crossing",
                    "kind": "ARENA",
                    "name": "斑马线",
                    "parent_id": "road",
                    "bounds": {"x": 4, "y": 2, "width": 1, "height": 3},
                },
                {
                    "id": "pedestrian-signal",
                    "kind": "GAME_OBJECT",
                    "name": "行人信号灯",
                    "parent_id": "crossing",
                    "bounds": {"x": 3, "y": 4, "width": 1, "height": 1},
                    "interaction_mode": "SKILL_BOUND",
                    "skill_bindings": [
                        {
                            "interaction_key": "query-pedestrian-signal",
                            "skill_name": "traffic-signal-state",
                            "description": "查询当前行人信号",
                            "interaction_radius_tiles": 2.5,
                            "default_request": "现在可以过马路吗？",
                        }
                    ],
                    "initial_state": {
                        "signal_cycle": {"red_steps": 1, "green_steps": 2}
                    },
                },
            ]
        }
    }


class _PassiveAgent:
    """为 ``_PassiveAgent`` 相关场景组织共享测试状态、输入或断言。"""
    agent_key = "pedestrian"
    name = "林晓"
    coord = (4, 5)

    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.selection = "NONE"
        self.observations = []

    def choose_game_object_interaction(self, options, _planned_path):
        """为本测试模块封装 ``choose_game_object_interaction`` 辅助步骤，减少重复的场景搭建代码。"""
        return self.selection

    def receive_game_object_observation(self, **observation):
        """为本测试模块封装 ``receive_game_object_observation`` 辅助步骤，减少重复的场景搭建代码。"""
        self.observations.append(observation)
        return "WAIT" if "红灯" in observation["response"] else "CONTINUE"

    def get_event(self):
        """为本测试模块封装 ``get_event`` 辅助步骤，减少重复的场景搭建代码。"""
        return Event(self.name, "正在", "前往马路北侧")










def test_editor_game_object_initial_state_is_real_even_without_a_passive_skill():
    clock = SimulationClock(datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc))
    world = {
        "editor_v2": {
            "hierarchy_nodes": [
                {
                    "id": "world",
                    "kind": "WORLD",
                    "name": "测试世界",
                    "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                },
                {
                    "id": "fault-light",
                    "kind": "GAME_OBJECT",
                    "parent_id": "world",
                    "name": "故障红灯",
                    "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "initial_state": {"signal": "RED", "powered": True},
                    "skill_bindings": [],
                },
            ]
        }
    }

    system = GameObjectInteractionSystem(world, clock=clock)

    assert system.affordances == ()
    assert system.object_state("fault-light") == {
        "signal": "RED",
        "powered": True,
    }
    before, after = system.apply_state_patch("fault-light", {"powered": False})
    assert before["powered"] is True
    assert after == {"signal": "RED", "powered": False}


def test_spatial_asset_footprint_and_interaction_radius_share_tile_units():
    clock = SimulationClock(datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc))
    world = {
        "spatial_scene": {
            "schema_version": "ga-spatial-scene/v2",
            "palette_refs": {},
            "placements": [
                {
                    "instance_key": "wide-desk",
                    "spatial_asset_id": "desk-asset",
                    "x_tiles": 5,
                    "y_tiles": 5,
                }
            ],
        },
        "editor": {
            "spatial_assets": {
                "desk-asset": {
                    "kind": "OBJECT",
                    "name": "三格办公桌",
                    "physics": {"width_tiles": 3, "height_tiles": 1},
                    "initial_state": {},
                    "skill_bindings": [
                        {
                            "interaction_key": "inspect",
                            "skill_name": "inspect-desk",
                            "interaction_radius_tiles": 0.5,
                        }
                    ],
                }
            }
        },
    }

    system = GameObjectInteractionSystem(world, clock=clock)

    assert system.affordances[0].bounds == (4.0, 5.0, 3.0, 1.0)
    assert len(system.nearby((4, 5))) == 1
    assert system.nearby((3, 5)) == []


def test_interaction_selection_prepares_request_without_running_object_in_agent_identity():
    """An Agent queues a request; the object's own iteration performs execution."""
    clock = SimulationClock(datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc))
    system = GameObjectInteractionSystem(_world(), clock=clock)
    agent = _PassiveAgent()

    nearby = system.nearby(agent.coord)

    assert [item.interaction_key for item in nearby] == ["query-pedestrian-signal"]
    first = system.interact_selected(
        agent, nearby[0].selection_key, step_no=1
    )
    second = system.interact_selected(
        agent, nearby[0].selection_key, step_no=2
    )

    assert first["agent_decision"] == second["agent_decision"] == "PENDING"
    assert "response" not in first and "response" not in second
    assert first["agent_key"] == agent.agent_key
