"""Regressions for current shared kernel and Studio surfaces."""

from __future__ import annotations
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from generative_agents.modules.game_object_interaction import GameObjectInteractionSystem
from generative_agents.modules.memory import Event
from generative_agents.runtime.context import RunControl, SimulationClock
from generative_agents.skills import SkillRegistry, SnapshotPassiveSkillRuntime
from generative_agents.start import SimulationRunner

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


class _CountingPassiveRuntime:
    """为 ``_CountingPassiveRuntime`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.runtime = SnapshotPassiveSkillRuntime(SkillRegistry().snapshot())
        self.calls = 0

    def run(self, *args, **kwargs):
        """为本测试模块封装 ``run`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls += 1
        return self.runtime.run(*args, **kwargs)


class _TextSkillModel:
    def __init__(self):
        self.requests = []

    def chat_completion(self, messages, **kwargs):
        self.requests.append({"messages": messages, **kwargs})
        return {"content": "洗手台反馈：已出水，可以洗漱。"}


def test_crosswalk_signal_advisor_returns_all_three_phases_in_natural_language():
    """回归验证 ``test_crosswalk_signal_advisor_returns_all_three_phases_in_natural_language`` 所描述的业务结果、故障边界和隔离约束。"""
    runtime = SnapshotPassiveSkillRuntime(SkillRegistry().snapshot())
    context = {
        "object_state": {
            "crossing_name": "西侧人行横道",
            "signal_cycle": {
                "red_steps": 2,
                "green_steps": 2,
                "flashing_steps": 1,
            },
        }
    }

    red = runtime.run(
        "crosswalk-signal-advisor",
        "现在可以过马路吗？",
        context={**context, "step_no": 1},
    )
    green = runtime.run(
        "crosswalk-signal-advisor",
        "现在可以过马路吗？",
        context={**context, "step_no": 3},
    )
    flashing = runtime.run(
        "crosswalk-signal-advisor",
        "现在可以过马路吗？",
        context={**context, "step_no": 5},
    )

    assert "行人红灯" in red.output_text
    assert "行人绿灯" in green.output_text
    assert "绿灯闪烁清空期" in flashing.output_text


def test_game_object_can_bind_a_text_only_skill(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    document = registry.create(
        name="sink-response",
        description="根据交互请求和对象状态用自然语言反馈。",
        kind="atomic",
    )
    snapshot = registry.snapshot([document.name])
    model = _TextSkillModel()
    runtime = SnapshotPassiveSkillRuntime(
        snapshot,
        registry=registry,
        model_config={"model": "test-model", "enable_thinking": False},
        model_client=model,
    )

    result = runtime.run(
        "sink-response",
        "我想洗漱",
        context={
            "step_no": 3,
            "virtual_time": "2026-08-28T07:10:00+08:00",
            "agent": {"agent_key": "lin-chen", "name": "林晨"},
            "game_object": {"object_key": "sink-1", "name": "洗手台"},
            "object_state": {"water": "available"},
        },
    )

    assert result.output_text == "洗手台反馈：已出水，可以洗漱。"
    assert result.content_hash == document.content_hash
    assert [item["event"] for item in result.trace] == [
        "game_object_skill.start",
        "skill.start",
        "skill.result",
        "game_object_skill.result",
    ]
    request = model.requests[0]
    assert '"object_key": "sink-1"' in request["messages"][1]["content"]
    assert request["agent_key"] == "lin-chen"
    assert request["step_no"] == 3


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
    runtime = _CountingPassiveRuntime()
    system = GameObjectInteractionSystem(_world(), clock=clock)
    agent = _PassiveAgent()

    nearby = system.nearby(agent.coord)

    assert [item.interaction_key for item in nearby] == ["query-pedestrian-signal"]
    assert runtime.calls == 0
    first = system.interact_selected(
        agent, nearby[0].selection_key, step_no=1
    )
    second = system.interact_selected(
        agent, nearby[0].selection_key, step_no=2
    )

    assert runtime.calls == 0
    assert first["agent_decision"] == second["agent_decision"] == "PENDING"
    assert "response" not in first and "response" not in second
    assert first["agent_key"] == agent.agent_key


class _Tile:
    """为 ``_Tile`` 相关场景组织共享测试状态、输入或断言。"""
    def get_address(self):
        """为本测试模块封装 ``get_address`` 辅助步骤，减少重复的场景搭建代码。"""
        return ["过街演示", "道路", "斑马线", "南侧候行区"]


class _RunnerAgent:
    """为 ``_RunnerAgent`` 相关场景组织共享测试状态、输入或断言。"""
    name = "林晓"

    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.coord = (4, 5)
        self.path = []
        self._event = Event(
            self.name,
            "正在",
            "通过斑马线前往北侧",
            address=["过街演示", "道路", "斑马线", "北侧出口"],
        )

    def move(self, coord, path=None):
        """为本测试模块封装 ``move`` 辅助步骤，减少重复的场景搭建代码。"""
        self.coord = tuple(coord)
        self.path = list(path or ())

    def get_event(self, as_act=True):
        """为本测试模块封装 ``get_event`` 辅助步骤，减少重复的场景搭建代码。"""
        return self._event if as_act else None

    def get_tile(self):
        """为本测试模块封装 ``get_tile`` 辅助步骤，减少重复的场景搭建代码。"""
        return _Tile()


class _InteractiveRunnerGame:
    """为 ``_InteractiveRunnerGame`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.agent = _RunnerAgent()
        self.agents = {"pedestrian": self.agent}
        self.agent_keys_by_name = {"林晓": "pedestrian"}
        self.route = [(4, 4), (4, 3), (4, 2), (4, 1)]

    def reset_game(self):
        """为本测试模块封装 ``reset_game`` 辅助步骤，减少重复的场景搭建代码。"""
        pass

    def get_agent(self, _agent_key):
        """为本测试模块封装 ``get_agent`` 辅助步骤，减少重复的场景搭建代码。"""
        return self.agent

    def agent_think(
        self,
        _agent_key,
        status,
        *,
        step_no,
        total_steps,
        stride_minutes,
    ):
        """为本测试模块封装 ``agent_think`` 辅助步骤，减少重复的场景搭建代码。"""
        self.agent.move(status["coord"], status.get("path"))
        route = list(self.agent.path or self.route)
        decision = "WAIT" if step_no == 1 else "MOVE"
        response = "当前为行人红灯，请等待。" if step_no == 1 else "当前为行人绿灯，可以通行。"
        return {
            "plan": {"path": route if decision == "MOVE" else []},
            "world_action": {
                "action_type": decision,
                "arguments": {"action_type": decision},
                "path": route if decision == "MOVE" else [],
            },
            "info": {
                "currently": "准备过马路",
                "associate": {},
                "concepts": {},
                "action": {},
                "schedule": {},
                "external_observations": [{
                    "object_key": "pedestrian-signal",
                    "response": response,
                    "agent_decision": "WAIT" if step_no == 1 else "CONTINUE",
                }],
            },
            "events": ({
                "kind": "game_object_interaction",
                "agent_key": "pedestrian",
                "object_key": "pedestrian-signal",
                "object_name": "行人信号灯",
                "interaction_key": "query-pedestrian-signal",
                "skill_name": "traffic-signal-state",
                "skill_revision": "revision-demo",
                "request": "现在可以过马路吗？",
                "response": response,
                "agent_decision": "COMPLETED",
                "location": ("过街演示", "道路", "斑马线"),
            },),
        }

    def commit_world_action(
        self,
        _agent_key,
        outcome,
        *,
        stride_minutes,
        movement_budget,
    ):
        """提交测试 Brain 已选择的单一世界动作。"""
        planned = tuple(tuple(coord) for coord in outcome["world_action"]["path"])
        consumed = planned[:movement_budget]
        remaining = planned[len(consumed):]
        origin = tuple(self.agent.coord)
        if consumed:
            self.agent.move(consumed[-1], remaining)
        executed = (origin, *consumed) if consumed else ()
        return {
            "outcome": outcome,
            "planned_path": planned,
            "executed_path": executed,
            "remaining_path": remaining,
        }


class _Committer:
    """为 ``_Committer`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.results = []

    def commit(self, result, *, force_checkpoint):
        """为本测试模块封装 ``commit`` 辅助步骤，减少重复的场景搭建代码。"""
        self.results.append(result)


def test_runner_waits_on_red_then_crosses_on_green_and_records_the_exchange():
    """回归验证 ``test_runner_waits_on_red_then_crosses_on_green_and_records_the_exchange`` 所描述的业务结果、故障边界和隔离约束。"""
    context = SimpleNamespace(
        run_id=uuid4(),
        attempt_id=uuid4(),
        clock=SimulationClock(datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)),
        control=RunControl(),
        algorithm=SimpleNamespace(movement_tiles_per_minute=4),
    )
    game = _InteractiveRunnerGame()
    committer = _Committer()
    runner = SimulationRunner(context, game, committer)

    runner.run(2, stride_minutes=1)

    red, green = committer.results
    assert red.agents[0].to_coord == (4, 5)
    assert red.agents[0].decision_context["external_observations"][0][
        "agent_decision"
    ] == "WAIT"
    assert [event.event_type for event in red.domain_events] == [
        "GAME_OBJECT_INTERACTION_REQUESTED",
        "GAME_OBJECT_SKILL_RESPONDED",
    ]
    assert red.domain_events[0].payload["location"] == "过街演示:道路:斑马线"
    assert green.agents[0].to_coord == (4, 1)
    assert green.agents[0].decision_context["external_observations"][0][
        "agent_decision"
    ] == "CONTINUE"
