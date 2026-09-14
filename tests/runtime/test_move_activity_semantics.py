"""MOVE activity must survive commit, bounded perception, restore and replay."""

import copy
import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.modules.agent import Agent
from generative_agents.modules.game import Game
from generative_agents.modules.game_object_interaction import GameObjectInteractionSystem
from generative_agents.modules.maze import Maze
from generative_agents.modules.memory import Action, Event
from generative_agents.runtime.capabilities import SimulationMCPServer
from generative_agents.runtime.context import SimulationClock
from generative_agents.runtime.iteration import IterationContext, ObjectIterationContext
from generative_agents.runtime.object_skills import ObjectMCPServer
from generative_agents.runtime.replay_v2 import _step_document
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.runtime.results import StepResult, StepResultBuilder


def _game():
    bounds = {"x": 0, "y": 0, "width": 13, "height": 5}
    nodes = [
        {"id": "world", "kind": "WORLD", "name": "世界", "parent_id": None, "bounds": bounds},
        {"id": "sector", "kind": "SECTOR", "name": "街区", "parent_id": "world", "bounds": bounds},
        {"id": "road", "kind": "ARENA", "name": "道路", "parent_id": "sector",
         "bounds": {"x": 0, "y": 0, "width": 10, "height": 5}},
        {"id": "hidden", "kind": "ARENA", "name": "视野外地点", "parent_id": "sector",
         "bounds": {"x": 10, "y": 0, "width": 3, "height": 5}},
        {"id": "camera", "kind": "GAME_OBJECT", "name": "摄像头", "parent_id": "road",
         "bounds": {"x": 5, "y": 0, "width": 1, "height": 1},
         "skill_bindings": [{"skill_name": "observe", "vision_radius": 2, "attention_bandwidth": 8}]},
    ]
    world = {"world": "世界", "size": [5, 13], "tile_size": 32,
             "tile_address_keys": ["world", "sector", "arena", "game_object"],
             "editor_v2": {"root_node_id": "world", "hierarchy_nodes": nodes},
             "tiles": [{"coord": [x, y], "collision": False,
                        "address": ["世界", "街区", "道路" if x < 10 else "视野外地点"]
                        + (["摄像头"] if (x, y) == (5, 0) else [])}
                       for y in range(5) for x in range(13)]}
    game = Game.__new__(Game)
    game.context = SimpleNamespace(run_id=uuid4(), attempt_id=uuid4(),
                                   clock=SimulationClock(datetime(2026, 9, 13, 8, tzinfo=timezone.utc)))
    game.maze = Maze(world, logging.getLogger(__name__), random.Random(42))
    game.game_object_interactions = GameObjectInteractionSystem(world, clock=game.context.clock)
    game._conversation_threads = {}
    game.agents = {}
    for key, coord, vision in [("rider", (0, 2), 20), ("peer", (5, 1), 2)]:
        actor = Agent.__new__(Agent)
        actor.agent_key = actor.name = key
        actor.maze, actor.coord, actor.path = game.maze, None, []
        actor.scratch = SimpleNamespace(currently="等待")
        actor.percept_config = {"vision_r": vision, "att_bandwidth": 8}
        actor.action = Action(Event(key, "等待", "下一轮", address=game.maze.tile_at(coord).get_address()),
                              clock=game.context.clock)
        actor.move(coord, [])
        game.agents[key] = actor
    return game


def _server(game, key="rider", step=1):
    agent = game.agents[key]
    return SimulationMCPServer(game, IterationContext(
        run_id=game.context.run_id, attempt_id=game.context.attempt_id,
        agent_key=key, agent_name=agent.name, step_no=step, total_steps=5,
        now=game.context.clock.get_date(), stride_minutes=1, coord=agent.coord,
        address=tuple(agent.get_tile().get_address()),
        spatial_semantics=tuple(agent.get_tile().spatial_semantics),
    ))


def _camera(game, result):
    binding = game.game_object_interactions.affordances[0]
    return ObjectMCPServer(game, ObjectIterationContext(
        run_id=result.run_id, attempt_id=result.attempt_id, object_key="camera", object_name="摄像头",
        step_no=result.step_no, total_steps=5, now=result.virtual_time, stride_minutes=1,
        coord=(5, 0), address=("世界", "街区", "道路", "摄像头"),
    ), binding=binding, observed_facts=result.domain_events)


def _call(server, name, arguments):
    response = server.call(name, arguments)
    assert not response["isError"], response
    return json.loads(response["content"][0]["text"])


def _move(game, phrase=None, *, budget=6, target=(12, 2), **extra):
    server = _server(game)
    request = {"action_type": "MOVE", "target_coord": list(target), **extra}
    if phrase is not None:
        request["predicate"] = phrase
    before = game.agents["rider"].coord
    _call(server, "world-act", request)
    committed = game.commit_world_action("rider", {"world_action": server.action.as_dict()},
                                        stride_minutes=1, movement_budget=budget)
    builder = StepResultBuilder(game.context.run_id, game.context.attempt_id, 1, game.context.clock.get_date())
    collector = StepResultCollector(builder, name_to_key={key: key for key in game.agents})
    collector.capture_agent("rider", game.agents["rider"], before, committed["outcome"],
                            executed_path=committed["executed_path"], planned_path=committed["planned_path"],
                            remaining_path=committed["remaining_path"])
    for event in committed["outcome"]["events"]:
        collector.capture_event(event)
    return builder.freeze()


@pytest.mark.parametrize("phrase", ["骑电动自行车前行", "步行", "推着自行车前行", "滑行"])
def test_move_activity_commits_to_current_state_perception_and_replay(phrase):
    game = _game()
    result = _move(game, phrase, description="已经抵达视野外地点并完成任务", object="视野外地点")
    expected = {"text": phrase, "source": "actor_declared"}
    fact = result.domain_events[0]
    payload = fact.payload["structured_payload"]
    assert fact.payload["predicate"] == "移动到"
    assert fact.payload["object"] == "世界:街区:道路"
    assert payload["movement_activity"] == result.agents[0].action.movement_activity == expected
    assert payload["to_coord"] == [6, 2] and not payload["reached_target"]
    assert payload["remaining_path"][-1] == [12, 2]
    assert payload["arguments"]["requested_description"] == "已经抵达视野外地点并完成任务"
    assert "视野外地点" not in payload["description"]
    assert "完成任务" not in payload["description"]
    assert phrase in payload["currently"] == game.agents["rider"].scratch.currently
    assert payload["emoji"] == "➡️"

    perception = _call(_server(game, "peer", step=2), "world-perceive", {})
    nearby = next(item for item in perception["nearby_agents"] if item["agent_key"] == "rider")
    event = next(item for item in perception["events"] if item["subject"] == "rider")
    assert nearby["current_action"]["movement_activity"] == event["movement_activity"] == expected
    assert "视野外地点" not in json.dumps(perception, ensure_ascii=False)
    seen = _call(_camera(game, result), "world-perceive", {"radius_tiles": 100})
    movement = seen["observed_actions"][0]
    assert movement["movement_activity"] == expected
    assert movement["visible_segments"] == [[[3, 2], [4, 2], [5, 2], [6, 2]]]
    assert "视野外地点" not in json.dumps(seen, ensure_ascii=False)

    # Serialize exactly the committed fact, then use both replay projections.
    restored = StepResult.from_dict(json.loads(json.dumps(result.to_dict(), ensure_ascii=False)))
    replay = _step_document(restored, checkpoint=True, attempt_boundary=True)
    assert replay["agents"][0]["action"]["movement_activity"] == expected
    assert replay["agents"][0]["action"]["description"] == payload["description"]
    reader = ReplayReader.__new__(ReplayReader)
    reader.iter_steps = lambda **kwargs: iter([restored.to_dict()])
    assert reader.state_at(1)["agents"]["rider"]["action"]["movement_activity"] == expected

    # Restore the actual Action serializer used in Agent checkpoints and reindex it.
    actor = game.agents["rider"]
    actor.action = Action.from_dict(json.loads(json.dumps(actor.action.to_dict())), clock=game.context.clock)
    actor.move(actor.coord, actor.path)
    after_restore = _call(_server(game, "peer", step=2), "world-perceive", {})
    assert after_restore["nearby_agents"][0]["current_action"]["movement_activity"] == expected


def test_camera_preserves_activity_after_rider_exits_view_without_leaking_destination():
    game = _game()
    result = _move(game, "骑电动自行车前行", budget=12)
    assert result.domain_events[0].payload["structured_payload"]["reached_target"] is True
    seen = _call(_camera(game, result), "world-perceive", {})
    assert not any(item["agent_key"] == "rider" for item in seen["nearby_agents"])
    movement = seen["observed_actions"][0]
    assert movement["movement_activity"]["text"] == "骑电动自行车前行"
    assert movement["visible_segments"] == [[[3, 2], [4, 2], [5, 2], [6, 2], [7, 2]]]
    assert set(movement) == {"fact_id", "event_type", "agent_key", "step_no", "predicate", "object",
                             "visible_segments", "movement_activity"}
    assert "视野外地点" not in json.dumps(seen, ensure_ascii=False)
    assert [12, 2] not in [coord for segment in movement["visible_segments"] for coord in segment]


def test_missing_move_activity_is_unknown_and_never_inferred_from_description_or_audit():
    game = _game()
    result = _move(game, description="骑行")
    wire = result.to_dict()
    wire["domain_events"][0]["payload"]["structured_payload"]["arguments"]["requested_predicate"] = "骑行"
    result = StepResult.from_dict(wire)
    payload = result.domain_events[0].payload["structured_payload"]
    assert payload["movement_activity"] is None
    assert result.agents[0].action.movement_activity is None
    assert "骑行" not in result.agents[0].action.description
    assert "🚶" != result.agents[0].action.emoji
    assert _call(_camera(game, result), "world-perceive", {})["observed_actions"][0]["movement_activity"] is None


@pytest.mark.parametrize("predicate", [42, {"text": "骑行"}, "x" * 241, "骑行\n前进"])
def test_invalid_activity_does_not_select_an_action_or_change_the_world(predicate):
    game = _game()
    server = _server(game)
    before = game.agents["rider"].action.to_dict()
    response = server.call("world-act", {"action_type": "MOVE", "target_coord": [6, 2], "predicate": predicate})
    assert response["isError"] and server.action is None
    assert game.agents["rider"].coord == (0, 2)
    assert game.agents["rider"].action.to_dict() == before


@pytest.mark.parametrize("field", ["movement_activity", "reached_target", "to_coord", "predicate"])
def test_step_result_rejects_activity_or_displacement_disagreement(field):
    wire = _move(_game(), "步行").to_dict()
    event = wire["domain_events"][0]["payload"]
    if field == "movement_activity":
        event["structured_payload"][field]["text"] = "骑行"
    elif field == "reached_target":
        event["structured_payload"][field] = True
    elif field == "to_coord":
        event["structured_payload"][field] = [12, 2]
    else:
        event[field] = "骑行"
    with pytest.raises(ValueError):
        StepResult.from_dict(wire)


def test_navigation_keeps_explicit_target_and_never_upgrades_a_first_tile_move():
    game = _game()
    server = _server(game)
    nav = _call(server, "world-navigate", {"target_coord": [12, 2]})
    assert nav["requested_target"] == {"target_coord": [12, 2]}
    assert nav["next_coord"] == [1, 2]
    assert nav["next_coord_role"] == "FIRST_PATH_TILE_NOT_DESTINATION"
    result = _move(game, "骑行", budget=6, target=nav["next_coord"])
    payload = result.domain_events[0].payload["structured_payload"]
    assert payload["to_coord"] == [1, 2] and payload["reached_target"] is True
    assert payload["arguments"]["target_coord"] == [1, 2]


def test_activity_belongs_to_this_move_and_is_not_carried_into_later_wait():
    game = _game()
    prior = _move(game, "推车")
    prior_wire = copy.deepcopy(prior.to_dict())
    server = _server(game, step=2)
    _call(server, "world-act", {"action_type": "WAIT"})
    game.commit_world_action("rider", {"world_action": server.action.as_dict()},
                             stride_minutes=1, movement_budget=6)
    assert game.agents["rider"].get_event().movement_activity is None
    assert prior.to_dict() == prior_wire
