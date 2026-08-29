"""基础能力回归测试：覆盖 ``test_game_object_skills`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import random
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from generative_agents.modules.game_object_interaction import (
    GameObjectInteractionSystem,
)
from generative_agents.modules.memory import Event
from generative_agents.modules.maze import Maze
from generative_agents.runtime.context import RunControl, SimulationClock
from generative_agents.runtime.replay_v2 import build_replay_v2
from generative_agents.skills import SkillRegistry, SnapshotPassiveSkillRuntime
from generative_agents.start import SimulationRunner
from generative_agents.config.schema import ExperimentDefinition, make_blank_definition
from generative_agents.web.app import create_app
from tests.support import brain_revision_via_api
from tools.seed_pedestrian_crossing_skill_demo import build_agent, build_world


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
                            "interaction_radius_m": 2.5,
                            "default_request": "现在可以过马路吗？",
                        }
                    ],
                    "extensions": {
                        "state": {
                            "signal_cycle": {"red_steps": 1, "green_steps": 2}
                        }
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
    assert result.revision == document.revision
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


def test_proximity_only_exposes_affordance_until_agent_explicitly_selects_it():
    """回归验证 ``test_proximity_only_exposes_affordance_until_agent_explicitly_selects_it`` 所描述的业务结果、故障边界和隔离约束。"""
    clock = SimulationClock(datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc))
    runtime = _CountingPassiveRuntime()
    system = GameObjectInteractionSystem(_world(), skill_executor=runtime, clock=clock)
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

    assert runtime.calls == 2
    assert first["agent_decision"] == "COMPLETED"
    assert "行人红灯" in first["response"]
    assert second["agent_decision"] == "COMPLETED"
    assert "行人绿灯" in second["response"]


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


def test_demo_resources_materialize_through_public_apis(database_url):
    """回归验证 ``test_demo_resources_materialize_through_public_apis`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        public_map = client.post(
            "/api/v1/maps",
            json={
                "map_key": "pedestrian-crossing-skill-demo",
                "name": "行人过街 Skill 演示",
                "width": 9,
                "height": 7,
            },
        )
        assert public_map.status_code == 201, public_map.text
        map_draft = client.get(
            f"/api/v1/maps/{public_map.json()['id']}/draft"
        ).json()
        saved_map = client.put(
            f"/api/v1/maps/{public_map.json()['id']}/draft",
            json={"lock_version": map_draft["lock_version"], "world": build_world()},
        )
        assert saved_map.status_code == 200, saved_map.text
        map_revision = client.post(
            f"/api/v1/maps/{public_map.json()['id']}/draft/publish",
            json={
                "draft_revision_id": saved_map.json()["id"],
                "lock_version": saved_map.json()["lock_version"],
            },
        )
        assert map_revision.status_code == 200, map_revision.text

        agent = client.post(
            "/api/v1/agent-templates",
            json={"definition": build_agent(), "description": "行人过街演示 Agent"},
        )
        assert agent.status_code == 201, agent.text
        agent_draft = client.get(
            f"/api/v1/agent-templates/{agent.json()['id']}/draft"
        ).json()
        agent_revision = client.post(
            f"/api/v1/agent-templates/{agent.json()['id']}/draft/publish",
            json={
                "draft_revision_id": agent_draft["id"],
                "lock_version": agent_draft["lock_version"],
            },
        )
        assert agent_revision.status_code == 200, agent_revision.text
        assert {
            item["code"] for item in agent_revision.json()["validation"]["warnings"]
        } == {
            "AGENT_PORTRAIT_ASSET_MISSING",
            "AGENT_SPRITE_ASSET_MISSING",
        }

        crowd = client.post(
            "/api/v1/crowds",
            json={
                "name": "行人过街演示人群",
                "crowd_key": "pedestrian-crossing-demo-crowd",
                "agent_revision_ids": [agent_revision.json()["id"]],
            },
        )
        assert crowd.status_code == 201, crowd.text
        crowd_draft = client.get(
            f"/api/v1/crowds/{crowd.json()['id']}/draft"
        ).json()
        crowd_revision = client.post(
            f"/api/v1/crowds/{crowd.json()['id']}/draft/publish",
            json={
                "draft_revision_id": crowd_draft["id"],
                "lock_version": crowd_draft["lock_version"],
            },
        )
        assert crowd_revision.status_code == 200, crowd_revision.text

        experiment = client.post(
            "/api/v1/experiments",
            json={
                "name": "Game Object Skill 端到端实验：行人过街",
                "goal": "红灯等待，绿灯过街",
                "brain_skill": "stanford-town-brain",
                "brain_revision_id": brain_revision_via_api(client)["revision_id"],
                "map_revision_id": map_revision.json()["id"],
                "crowd_revision_ids": [crowd_revision.json()["id"]],
            },
        )
        assert experiment.status_code == 201, experiment.text
        draft = client.get(
            f"/api/v1/experiments/{experiment.json()['id']}/draft"
        )
        assert draft.status_code == 200, draft.text
        validation = client.post(
            f"/api/v1/experiments/{experiment.json()['id']}/draft/validate"
        )

    definition = draft.json()["definition"]
    signal = next(
        node
        for node in definition["world"]["definition"]["editor_v2"]["hierarchy_nodes"]
        if node["id"] == "pedestrian-signal"
    )
    assert definition["world"]["map_revision_id"] == map_revision.json()["id"]
    assert definition["agents"][0]["agent_key"] == "pedestrian-lin-xiao"
    assert definition["agents"][0]["coord"] == [4, 5]
    assert signal["skill_bindings"][0]["skill_name"] == "traffic-signal-state"
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True


def test_demo_map_editor_metadata_is_accepted_by_runtime_maze():
    """回归验证 ``test_demo_map_editor_metadata_is_accepted_by_runtime_maze`` 所描述的业务结果、故障边界和隔离约束。"""
    definition = build_world()["definition"]

    maze = Maze(definition, logging.getLogger("pedestrian-crossing-test"), random.Random(7))

    assert maze.tile_at((3, 4)).get_address()[-1] == "行人信号灯"
    assert maze.tile_at((4, 5)).get_address()[-1] == "门口"


def test_demo_map_and_signal_have_a_self_contained_replay_renderer():
    """回归验证 ``test_demo_map_and_signal_have_a_self_contained_replay_renderer`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = make_blank_definition(key="crossing-replay", name="过街回放").model_dump(
        mode="json"
    )
    payload["world"] = build_world()
    payload["agents"] = [build_agent()]
    definition = ExperimentDefinition.model_validate(payload)

    replay = build_replay_v2(
        run_id=str(uuid4()),
        revision_id=str(uuid4()),
        definition_hash="demo-definition-hash",
        definition=definition,
        source_step=0,
        partial=True,
        results=(),
    )

    render_asset = replay["world"]["render_asset"]
    signal = next(
        item for item in render_asset["objects"] if item["instance_key"] == "pedestrian-signal"
    )
    assert render_asset["status"] == "READY"
    assert render_asset["renderer"] == "SPATIAL_GRID"
    assert render_asset["palette"]["crosswalk"]["color"] == "#F5F1E8"
    assert signal["appearance"]["emoji"] == "🚦"
    assert replay["agents"][0]["role"] == "PEDESTRIAN"
