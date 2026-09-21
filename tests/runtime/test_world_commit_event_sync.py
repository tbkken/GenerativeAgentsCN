"""World perception must expose the latest committed action at its real location."""

from __future__ import annotations

import copy
import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_runtime.engine.actor import ActorState as Agent
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.engine.space import Maze
from generative_agents.ga_runtime.memory.action import Action
from generative_agents.ga_runtime.memory.event import Event
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_runtime.engine.iteration import IterationContext


def _game():
    maze = Maze(
        {
            "world": "测试住宅",
            "size": [1, 4],
            "size_unit": "TILE",
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [
                {"coord": [x, 0], "collision": False, "address": address}
                for x, address in enumerate(
                    [
                        ["住宅", "卧室", "床边"],
                        ["住宅", "通道"],
                        ["住宅", "书房"],
                        ["住宅", "书房", "工作位"],
                    ]
                )
            ],
        },
        logging.getLogger(__name__),
        random.Random(1),
    )
    clock = SimpleNamespace(
        get_date=lambda: datetime(2026, 9, 9, 7, tzinfo=timezone.utc)
    )
    agent = Agent.__new__(Agent)
    agent.name = "测试人物"
    agent.agent_key = "agent-1"
    agent.maze = maze
    agent.coord = None
    agent.path = []
    agent.profile = SimpleNamespace(currently="刚刚醒来")
    agent.percept_config = {"vision_r": 4, "att_bandwidth": 20}
    agent.action = Action(
        Event(agent.name, "醒来", "床边", address=maze.tile_at((0, 0)).get_address()),
        clock=clock,
    )
    agent.move((0, 0), [])
    game = Game.__new__(Game)
    game.maze = maze
    game.agents = {agent.agent_key: agent}
    game.context = SimpleNamespace(clock=clock)
    game._conversation_threads = {}
    game.game_object_interactions = SimpleNamespace(
        object_state=lambda _key: {}, nearby=lambda _coord: []
    )
    return game, agent


def _perceived_actor_events(game, agent):
    iteration = IterationContext(
        run_id=uuid4(),
        attempt_id=uuid4(),
        agent_key=agent.agent_key,
        agent_name=agent.name,
        step_no=2,
        total_steps=5,
        now=game.context.clock.get_date(),
        stride_minutes=3,
        coord=tuple(agent.coord),
        address=tuple(agent.get_tile().get_address()),
        spatial_semantics=tuple(agent.get_tile().spatial_semantics),
    )
    response = SimulationMCPServer(game, iteration).call("world-perceive", {})
    assert not response["isError"], response
    perception = json.loads(response["content"][0]["text"])
    return [event for event in perception["events"] if event["subject"] == agent.name]


def _commit(game, agent, action_type, **arguments):
    path = arguments.pop("path", [])
    budget = arguments.pop("movement_budget", 12)
    return game.commit_world_action(
        agent.agent_key,
        {"world_action": {"action_type": action_type, "arguments": arguments, "path": path}},
        stride_minutes=3,
        movement_budget=budget,
    )


@pytest.mark.parametrize("budget,endpoint,remaining", [(1, (1, 0), [(2, 0), (3, 0)]), (12, (3, 0), [])])
def test_move_perception_matches_committed_endpoint_even_with_remaining_path(budget, endpoint, remaining):
    game, agent = _game()
    previous_event = agent.get_event()
    previous_payload = copy.deepcopy(previous_event.to_dict())

    committed = _commit(
        game, agent, "MOVE", target_coord=[3, 0],
        target_address=game.maze.tile_at((3, 0)).get_address(),
        path=[[1, 0], [2, 0], [3, 0]], movement_budget=budget,
    )

    fact = committed["outcome"]["events"][0]
    perceived = _perceived_actor_events(game, agent)
    assert len(perceived) == 1
    assert perceived[0]["coord"] == list(endpoint)
    assert perceived[0]["address"] == fact["structured_payload"]["after_address"]
    assert perceived[0]["predicate"] == fact["predicate"] == "移动到"
    assert perceived[0]["object"] == fact["object"]
    assert perceived[0]["describe"] == fact["structured_payload"]["description"]
    assert agent.path == remaining
    assert not any(event.subject == agent.name for event in game.maze.tile_at((0, 0)).get_events())
    assert previous_event.to_dict() == previous_payload
    assert previous_event is not agent.get_event()


def test_same_location_acts_refresh_perception_and_do_not_rewrite_prior_facts():
    game, agent = _game()
    first = _commit(game, agent, "ACT", predicate="穿好", object="衣服", description="穿好晨间衣服")
    first_fact = first["outcome"]["events"][0]
    first_payload = copy.deepcopy(first_fact)
    assert _perceived_actor_events(game, agent)[0]["predicate"] == "穿好"

    _commit(game, agent, "ACT", predicate="整理", object="床铺", description="整理床铺")

    perceived = _perceived_actor_events(game, agent)
    assert len(perceived) == 1
    assert perceived[0]["predicate"] == "整理"
    assert perceived[0]["coord"] == [0, 0]
    assert perceived[0]["address"] == agent.get_event().address
    assert first_fact == first_payload


def test_restored_action_is_perceivable_when_checkpoint_has_remaining_path():
    game, agent = _game()
    agent.coord = None
    agent.action = Action(
        Event(agent.name, "移动到", "通道", address=game.maze.tile_at((1, 0)).get_address()),
        clock=game.context.clock,
    )
    game.maze.tile_at((0, 0)).remove_events(subject=agent.name)

    # Agent initialization restores the committed coordinate and future path together.
    agent.move((1, 0), [(2, 0), (3, 0)])

    perceived = _perceived_actor_events(game, agent)
    assert len(perceived) == 1
    assert perceived[0]["coord"] == [1, 0]
    assert perceived[0]["predicate"] == "移动到"
    assert agent.path == [(2, 0), (3, 0)]


def test_move_after_object_change_does_not_carry_previous_object_event():
    game, agent = _game()
    game.game_object_interactions.apply_state_patch = lambda _key, _patch: ({}, {"state": "tidy"})
    _commit(game, agent, "SET_OBJECT_STATE", object_key="床边", state_patch={"state": "tidy"})
    old_object_event = agent.action.obj_event
    old_payload = copy.deepcopy(old_object_event.to_dict())

    _commit(
        game, agent, "MOVE", target_coord=[1, 0],
        target_address=game.maze.tile_at((1, 0)).get_address(), path=[[1, 0]],
    )

    assert agent.action.obj_event is None
    assert old_object_event.to_dict() == old_payload
    assert old_object_event not in game.maze.tile_at((1, 0)).get_events()
    assert _perceived_actor_events(game, agent)[0]["predicate"] == "移动到"


def test_actor_sync_preserves_another_agents_event_at_the_old_location():
    game, agent = _game()
    other_event = Event("同处人物", "等待", "下一事项", address=agent.get_tile().get_address())
    agent.get_tile().add_event(other_event)
    _commit(game, agent, "ACT", predicate="整理", object="床铺")
    _commit(
        game, agent, "MOVE", target_coord=[1, 0],
        target_address=game.maze.tile_at((1, 0)).get_address(), path=[[1, 0]],
    )

    assert other_event in game.maze.tile_at((0, 0)).get_events()
    assert other_event not in game.maze.tile_at((1, 0)).get_events()
