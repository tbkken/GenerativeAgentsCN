"""Object autonomy, bounded facts, interaction isolation and resumable commits."""

from __future__ import annotations

import copy
import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.schemas.object_skills import GameObjectSkillBinding
from generative_agents.ga_runtime.memory.stream import FileMemoryStream
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.engine.objects import GameObjectInteractionSystem
from generative_agents.ga_runtime.engine.space import Maze
from generative_agents.ga_runtime.memory.event import Event
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_runtime.engine.context import RunControl
from generative_agents.ga_runtime.engine.context import SimulationClock
from generative_agents.ga_runtime.engine.iteration import IterationContext
from generative_agents.ga_runtime.engine.iteration import ObjectIterationContext
from generative_agents.ga_runtime.skills.objects import ObjectMCPServer
from generative_agents.ga_runtime.skills.objects import ObjectSkillRuntime
from generative_agents.ga_protocol.schemas.facts import DomainEventRecord
from tests.skill_files import write_skill
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.engine.scheduler import SimulationRunner


def world():
    rect = {"x": 0, "y": 0, "width": 13, "height": 5}
    nodes = []
    parent = None
    for key, kind in [("world", "WORLD"), ("sector", "SECTOR"), ("road", "ARENA")]:
        nodes.append({"id": key, "name": key, "kind": kind, "parent_id": parent, "bounds": rect})
        parent = key
    nodes.append({"id": "camera", "name": "camera", "kind": "GAME_OBJECT", "parent_id": "road",
                  "bounds": {"x": 5, "y": 0, "width": 1, "height": 1},
                  "skill_bindings": [{"skill_name": "facility", "vision_radius": 2}],
                  "initial_state": {"state": "RED", "secret": "internal-only"}})
    return {"world": "world", "size": [5, 13], "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "editor_v2": {"root_node_id": "world", "hierarchy_nodes": nodes},
            "tiles": [{"coord": [x, y], "collision": False,
                       "address": ["world", "sector", "road"] + (["camera"] if (x, y) == (5, 0) else [])}
                      for y in range(5) for x in range(13)]}


class Rider:
    def __init__(self, maze, key="rider", coord=(0, 2)):
        self.maze, self.agent_key, self.name, self.coord = maze, key, key, coord
        self.path = []
        self.action = None
        self.profile = SimpleNamespace(currently="骑行")
        self.percept_config = {"vision_r": 2, "att_bandwidth": 8}

    def get_tile(self):
        return self.maze.tile_at(self.coord)

    def get_event(self, subject=True):
        if not subject:
            return None
        return self.action.event if self.action else Event(self.name, "骑行", "道路", address=self.get_tile().get_address())

    def move(self, coord, path=()):
        self.coord, self.path = tuple(coord), list(path)

    def to_dict(self):
        return {"coord": list(self.coord)}


class ToolModel:
    def __init__(self, choose):
        self.choose = choose
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), kwargs))
        tool, arguments = self.choose(messages, kwargs)
        if tool is None:
            return {"content": arguments}
        return {"content": "", "tool_calls": [{"id": f"call-{len(self.calls)}", "type": "function",
                 "function": {"name": tool, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def game_with_runtime(tmp_path, choose, *, agents=True):
    game = Game.__new__(Game)
    clock = SimulationClock(datetime(2026, 9, 13, 8, tzinfo=timezone.utc))
    game.context = SimpleNamespace(run_id=uuid4(), attempt_id=uuid4(), clock=clock,
                                   random=random.Random(42), control=RunControl(),
                                   algorithm=SimpleNamespace(movement_tiles_per_minute=12))
    game.logger = logging.getLogger(__name__)
    game.maze = Maze(world(), game.logger, game.context.random)
    game.game_object_interactions = GameObjectInteractionSystem(world(), clock=clock)
    game.agents = {"rider": Rider(game.maze)} if agents else {}
    game.agent_keys_by_name = {key: key for key in game.agents}
    game._external_observation_inbox = {key: [] for key in game.agents}
    game._conversation_threads, game._open_conversation_by_participants = {}, {}
    game._conversation_sequence = 0
    game.conversation = {}
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, name="facility", description="观察周围，按需记录活动和回复交互。", kind="atomic")
    # No script and no hand-written MCP tool names are required in this Skill.
    model = ToolModel(choose)
    stream = FileMemoryStream(tmp_path / "memory", run_id=game.context.run_id,
                              attempt_id=game.context.attempt_id, clock=lambda: clock.get_date())
    game.context.memory_stream = stream
    game.context.object_skill_runtime = ObjectSkillRuntime(
        registry, model_config={"model": "test"}, model_client=model, memory_stream=stream,
    )
    return game, model


class Committer:
    def __init__(self, game):
        self.game, self.results, self.snapshots = game, [], []

    def commit(self, result, *, force_checkpoint):
        self.results.append(result)
        self.snapshots.append(self.game.snapshot_state())


def runner(game, *, move=False, interaction=False, completed=0):
    def think(key, status, *, step_no, total_steps, stride_minutes):
        agent = game.agents[key]
        iteration = IterationContext(
            run_id=game.context.run_id, attempt_id=game.context.attempt_id,
            agent_key=key, agent_name=key, step_no=step_no, total_steps=total_steps,
            now=game.context.clock.get_date(), stride_minutes=stride_minutes,
            coord=agent.coord, address=tuple(agent.get_tile().get_address()),
            spatial_semantics=tuple(agent.get_tile().spatial_semantics),
        )
        mcp = SimulationMCPServer(game, iteration)
        args = ({"action_type": "MOVE", "target_coord": [12, 2]} if move and agent.coord != (12, 2)
                else {"action_type": "INTERACT", "selection_key": "camera/interact", "request": "现在什么状态？"}
                if interaction and key == "rider" else {"action_type": "WAIT"})
        result = mcp.call("world-act", args)
        assert not result["isError"], result
        return {"world_action": mcp.action.as_dict(), "info": {"iteration_context": iteration.as_dict()}}
    game.agent_think = think
    committer = Committer(game)
    instance = SimulationRunner(game.context, game, committer, completed_steps=completed)
    return instance, committer


def decoded_tool(messages):
    return json.loads(messages[-1]["content"])


def test_binding_alone_enables_model_driven_autonomy_and_state_commit(tmp_path):
    def choose(messages, args):
        assert "world-act" in {item["function"]["name"] for item in args["tools"]}
        assert args["agent_key"] == "game-object:camera"
        return "world-act", {"action_type": "SET_OBJECT_STATE", "state_patch": {"state": "GREEN"}}
    game, model = game_with_runtime(tmp_path, choose, agents=False)
    run, commit = runner(game)
    assert run.run(1, stride_minutes=1) == 1
    assert len(model.calls) == 1
    assert commit.results[0].agents == ()
    event = commit.results[0].domain_events[0]
    assert event.event_type == "GAME_OBJECT_STATE_CHANGED"
    assert event.payload["subject"] == "camera"
    assert event.payload["structured_payload"]["after"]["state"] == "GREEN"
    assert commit.snapshots[0]["game_object_runtime"]["camera"]["last_step"] == 1


def test_object_root_uses_model_even_when_its_bundle_contains_scripts(tmp_path):
    def choose(messages, args):
        names = {item["function"]["name"] for item in args["tools"]}
        assert "world-act" in names
        assert not any(name.startswith("legacy.") for name in names)
        return "world-act", {"action_type": "WAIT", "wait_reason": "本轮没有需处理的变化"}
    game, model = game_with_runtime(tmp_path, choose, agents=False)
    registry = game.context.object_skill_runtime.registry
    document = registry.get("facility")
    scripts = document.path.parent / "scripts"
    scripts.mkdir()
    for name in ("main.py", "legacy.py"):
        (scripts / name).write_text("raise AssertionError('object root must use its model')\n", encoding="utf-8")
    registry.get("facility").path.write_text(document.markdown + "\n历史附带资源：[旧程序](scripts/legacy.py)。本轮按正文执行。\n", encoding="utf-8")
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    assert len(model.calls) == 1
    assert commit.results[0].domain_events[0].event_type == "GAME_OBJECT_WAITED"


def test_unqueried_camera_observes_only_actual_visible_path_and_records_evidence(tmp_path):
    def choose(messages, args):
        if messages[-1]["role"] != "tool":
            return "world-perceive", {"radius_tiles": 100}
        seen = decoded_tool(messages)
        assert seen["radius_tiles"] == 2
        assert seen["nearby_agents"] == []  # Rider ended outside the sensor radius.
        facts = seen["observed_actions"]
        assert len(facts) == 1
        assert facts[0]["visible_segments"] == [[[3, 2], [4, 2], [5, 2], [6, 2], [7, 2]]]
        return "world-act", {"action_type": "ACT", "predicate": "抓拍", "object": "rider",
                             "target_agent_key": "rider", "evidence_ids": [facts[0]["fact_id"]],
                             "idempotency_key": "rider:first-passage"}
    game, model = game_with_runtime(tmp_path, choose)
    run, commit = runner(game, move=True)
    run.run(1, stride_minutes=1)
    events = commit.results[0].domain_events
    move = next(event for event in events if event.event_type == "AGENT_MOVED")
    capture = next(event for event in events if event.event_type == "GAME_OBJECT_ACTED")
    assert capture.payload["structured_payload"]["evidence"][0]["fact_id"] == str(move.event_id)
    assert game.consume_external_observations("rider") == ()
    assert len(model.calls) == 2


def test_interaction_reply_is_committed_once_and_only_to_requester_after_resume(tmp_path):
    def choose(messages, args):
        # Read the injected request without asking the rider to trigger execution.
        text = messages[1]["content"]
        start = text.index('{')
        context = json.loads(text[start:])["IterationContext"]
        requests = context["variables"]["interaction_requests"]
        return "world-act", {"action_type": "SET_OBJECT_STATE", "state_patch": {"state": "GREEN"},
                             "responses": [{"request_id": request["request_id"], "message": "当前绿灯"} for request in requests]}
    game, model = game_with_runtime(tmp_path, choose)
    game.agents["rider"].coord = (5, 1)
    game.agents["other"] = Rider(game.maze, "other", (12, 4))
    game._external_observation_inbox["other"] = []
    run, commit = runner(game, interaction=True)
    run.run(1, stride_minutes=1)
    assert len(model.calls) == 1
    assert len(game._external_observation_inbox["rider"]) == 1
    assert game._external_observation_inbox["other"] == []
    snapshot = game.snapshot_state()
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(snapshot)
    assert len(game.consume_external_observations("rider")) == 1
    assert game.consume_external_observations("rider") == ()
    run, after = runner(game, completed=1)
    run.run(1, stride_minutes=1)
    assert not any(event.event_type == "GAME_OBJECT_SKILL_RESPONDED" for event in after.results[0].domain_events)
    assert game.game_object_interactions.runtime_state("camera")["requests"] == []


def server_for(game, *, step=1, facts=(), requests=()):
    binding = game.game_object_interactions.affordances[0]
    iteration = ObjectIterationContext(
        run_id=game.context.run_id, attempt_id=game.context.attempt_id,
        object_key="camera", object_name="camera", step_no=step, total_steps=3,
        now=game.context.clock.get_date(), stride_minutes=1, coord=(5, 0),
        address=("world", "sector", "road", "camera"),
    )
    return ObjectMCPServer(game, iteration, binding=binding, observed_facts=facts,
                           requests=requests, memory_stream=game.context.memory_stream)


def test_object_and_agent_identities_state_permissions_and_private_memory(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: (None, ""))
    game.context.memory_stream.begin_step(1, game.context.clock.get_date())
    mcp = server_for(game)
    for tool, args in [
        ("world-perceive", {"agent_key": "rider"}),
        ("world-act", {"action_type": "MOVE", "target_coord": [2, 1]}),
        ("world-act", {"action_type": "SET_OBJECT_STATE", "object_key": "road", "state_patch": {"state": "x"}}),
        ("world-act", {"action_type": "WAIT", "responses": [{"request_id": "someone-elses", "message": "reply"}]}),
        ("world-act", {"action_type": "ACT", "predicate": "观察", "object": "rider", "target_agent_key": "rider"}),
    ]:
        assert mcp.call(tool, args)["isError"]
    assert not mcp.call("memory-stream-append", {"content": "object-private"})["isError"]
    assert game.context.memory_stream.search(agent_key="rider", query="object-private") == []
    assert len(game.context.memory_stream.search(agent_key="game-object:camera", query="object-private")) == 1
    assert not mcp.call("world-act", {"action_type": "WAIT"})["isError"]
    assert mcp.call("world-act", {"action_type": "WAIT"})["isError"]
    game.agents["rider"].coord = (5, 1)
    agent = game.agents["rider"]
    context = IterationContext(game.context.run_id, game.context.attempt_id, "rider", "rider", 1, 1,
                               game.context.clock.get_date(), 1, agent.coord, tuple(agent.get_tile().get_address()))
    rider_mcp = SimulationMCPServer(game, context)
    assert rider_mcp.call("world-act", {"action_type": "SET_OBJECT_STATE", "object_key": "camera",
                                        "state_patch": {"state": "OFF"}})["isError"]
    seen = json.loads(rider_mcp.call("world-perceive", {})["content"][0]["text"])
    assert next(obj for obj in seen["game_objects"] if obj["object_key"] == "camera")["state"] == {"state": "RED"}


def test_object_action_key_survives_checkpoint_and_new_attempt(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {
        "action_type": "ACT", "predicate": "记录", "object": "一次活动", "idempotency_key": "episode-1"}))
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(commit.snapshots[0])
    mcp = server_for(game, step=2)
    assert mcp.call("world-act", {"action_type": "ACT", "predicate": "记录", "object": "一次活动",
                                   "idempotency_key": "episode-1"})["isError"]
    assert not mcp.call("world-act", {"action_type": "WAIT"})["isError"]


def test_text_alone_never_changes_world_or_sends_a_response(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: (None, "已切换绿灯，已回复骑手"))
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    assert game.game_object_interactions.object_state("camera")["state"] == "RED"
    assert game.consume_external_observations("rider") == ()
    effect = next(item for item in commit.results[0].effects if item.payload.get("execution_source") == "OBJECT_SKILL_RUNTIME")
    assert effect.payload["fallback"]
    assert any(item["event"] == "object.missing_action" for item in effect.payload["trace"])


def test_invalid_bound_perception_configuration_is_rejected():
    binding = GameObjectSkillBinding(skill_name="camera")
    assert binding.interaction_key == "interact"
    with pytest.raises(ValueError):
        GameObjectSkillBinding(skill_name="camera", vision_radius=101)


def test_planned_path_outside_this_steps_movement_budget_is_not_observed(tmp_path):
    def choose(messages, _):
        if messages[-1]["role"] != "tool":
            return "world-perceive", {}
        assert decoded_tool(messages)["observed_actions"] == []
        return "world-act", {"action_type": "WAIT"}
    game, _ = game_with_runtime(tmp_path, choose)
    game.context.algorithm.movement_tiles_per_minute = 2
    run, commit = runner(game, move=True)
    run.run(1, stride_minutes=1)
    assert game.agents["rider"].coord == (2, 2)
    assert not any(event.event_type == "GAME_OBJECT_ACTED" for event in commit.results[0].domain_events)


def test_failed_object_model_rolls_back_memory_and_keeps_pending_requests(tmp_path):
    def choose(messages, _):
        if messages[-1]["role"] != "tool":
            return "memory-stream-append", {"content": "uncommitted-object-progress"}
        raise TimeoutError("model unavailable")
    game, _ = game_with_runtime(tmp_path, choose)
    game.agents["rider"].coord = (5, 1)
    run, commit = runner(game, interaction=True)
    run.run(1, stride_minutes=1)
    assert game.context.memory_stream.search(agent_key="game-object:camera", query="uncommitted-object-progress") == []
    assert len(game.game_object_interactions.runtime_state("camera")["requests"]) == 1
    assert game.consume_external_observations("rider") == ()
    effect = next(item for item in commit.results[0].effects if item.payload.get("execution_source") == "OBJECT_SKILL_RUNTIME")
    assert any(item["event"] == "object.fallback" for item in effect.payload["trace"])


def test_object_root_composes_natural_language_children_with_shared_identity(tmp_path):
    def choose(messages, args):
        names = {item["function"]["name"] for item in args.get("tools") or []}
        if args["prompt_key"] == "helper":
            assert "world-act" not in names
            assert args["agent_key"] == "game-object:camera"
            if messages[-1]["role"] != "tool":
                return "world-perceive", {}
            return None, "已观察周围，可记录一次观察。"
        if messages[-1]["role"] != "tool":
            return "call_skill", {"name": "helper", "input_text": "查看周围，给出建议。"}
        assert "已观察周围" in messages[-1]["content"]
        return "world-act", {"action_type": "ACT", "predicate": "观察", "object": "周围环境"}
    game, _ = game_with_runtime(tmp_path, choose)
    registry = game.context.object_skill_runtime.registry
    write_skill(registry, name="helper", description="查看周围并给出自然语言建议", kind="atomic")
    facility = registry.get("facility")
    registry.get("facility").path.write_text(facility.markdown + "\n先调用 $helper 获取建议，再决定行动。\n", encoding="utf-8")
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    assert sum(event.event_type == "GAME_OBJECT_ACTED" for event in commit.results[0].domain_events) == 1


def test_attention_limits_visible_facts_and_rejects_unobserved_evidence(tmp_path):
    from dataclasses import replace
    game, _ = game_with_runtime(tmp_path, lambda *_: (None, ""))
    facts = [DomainEventRecord(
        event_id=uuid4(), sequence=index + 1, event_type="AGENT_ACTED", agent_keys=("rider",),
        payload={"predicate": "停留", "object": "道路", "structured_payload": {"to_coord": coord}},
    ) for index, coord in enumerate(([5, 1], [6, 1], [12, 4]))]
    mcp = server_for(game, facts=facts)
    mcp.binding = replace(mcp.binding, attention_bandwidth=1)
    seen = json.loads(mcp.call("world-perceive", {})["content"][0]["text"])
    assert len(seen["observed_actions"]) == 1
    assert seen["attention"]["observed_action_candidates"] == 2
    assert seen["attention"]["truncated"]
    assert mcp.call("world-act", {"action_type": "ACT", "predicate": "记录", "object": "道路",
                                   "evidence_ids": [str(facts[2].event_id)]})["isError"]
