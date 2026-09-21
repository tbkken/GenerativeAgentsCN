"""Navigation accepts observed object addresses without revealing unseen objects."""

import copy
import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_runtime.engine.space import Maze
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_runtime.engine.iteration import IterationContext


HOME = ["测试住宅", "生活区"]
BED = [*HOME, "卧室", "床边"]
SHELF = [*HOME, "卧室", "资料架"]
COUNTER = [*HOME, "厨房", "料理台"]
OTHER_COUNTER = [*HOME, "工作间", "料理台"]


def navigation_server(*, vision=6, attention=6, blocked_door=False):
    width, height = 13, 7
    nodes = []

    def node(key, kind, address, bounds, parent_id=None):
        item = dict(id=key, kind=kind, name=address[-1], address=address,
                    semantic=address[-1], parent_id=parent_id,
                    bounds=dict(zip(("x", "y", "width", "height"), bounds)))
        nodes.append(item)
        return item

    world = node("home", "WORLD", HOME[:1], (0, 0, width, height))
    sector = node("sector", "SECTOR", HOME, (0, 0, width, height), "home")
    bedroom = node("bedroom", "ARENA", BED[:3], (0, 0, 5, height), "sector")
    kitchen = node("kitchen", "ARENA", COUNTER[:3], (5, 0, 4, height), "sector")
    workshop = node("workshop", "ARENA", OTHER_COUNTER[:3], (9, 0, 4, height), "sector")
    objects = {
        (1, 3): node("bed", "GAME_OBJECT", BED, (1, 3, 1, 1), "bedroom"),
        (3, 3): node("shelf", "GAME_OBJECT", SHELF, (3, 3, 1, 1), "bedroom"),
        (6, 3): node("counter", "GAME_OBJECT", COUNTER, (6, 3, 1, 1), "kitchen"),
        (11, 3): node("other-counter", "GAME_OBJECT", OTHER_COUNTER, (11, 3, 1, 1), "workshop"),
    }
    tiles = []
    for y in range(height):
        for x in range(width):
            arena = bedroom if x < 5 else kitchen if x < 9 else workshop
            semantics = [world, sector, arena]
            if (x, y) in objects:
                semantics.append(objects[x, y])
            tiles.append(dict(
                coord=[x, y], address=semantics[-1]["address"],
                spatial_semantics=semantics,
                collision=x == 4 and (blocked_door or y != 3),
            ))
    maze = Maze(dict(
        size=[height, width], tile_size=1, world=HOME[0], tiles=tiles,
        tile_address_keys=["world", "sector", "arena", "game_object"],
        semantic_index={"nodes": nodes},
    ), logging.getLogger(__name__), random.Random(7))
    agent = SimpleNamespace(
        name="测试角色", coord=(1, 3),
        percept_config={"vision_r": vision, "att_bandwidth": attention},
        spatial=SimpleNamespace(tree={HOME[0]: {HOME[1]: {"卧室": ["床边"]}}}),
    )
    agents = {"agent-1": agent}
    game = SimpleNamespace(
        maze=maze, agents=agents, get_agent=agents.__getitem__,
        game_object_interactions=SimpleNamespace(
            nearby=lambda _coord: [], object_state=lambda _key: {},
        ),
    )
    tile = maze.tile_at(agent.coord)
    iteration = IterationContext(
        uuid4(), uuid4(), "agent-1", agent.name, 1, 1,
        datetime(2026, 9, 9, tzinfo=timezone.utc), 1, agent.coord,
        tuple(tile.get_address()), tuple(tile.spatial_semantics),
    )
    return SimulationMCPServer(game, iteration), agent


def unpack(response):
    assert not response["isError"], response
    return json.loads(response["content"][0]["text"])


def perceive(server):
    return unpack(server.call("world-perceive", {}))


def navigate(server, address):
    return server.call("world-navigate", {"target_address": address})


def assert_unknown(response):
    assert response["isError"], response
    assert "not perceived or remembered by this Agent" in response["content"][0]["text"]


def test_observed_unremembered_object_is_navigable_and_navigation_is_readonly():
    server, agent = navigation_server()
    memory_before = copy.deepcopy(agent.spatial.tree)
    perception = perceive(server)
    assert COUNTER not in [item["address"] for item in perception["spatial_nodes"]]
    target = next(item for item in perception["game_objects"] if item["object_key"] == "counter")
    assert target["address"] == COUNTER and target["distance_tiles"] == 5
    assert target["relation"] == "NEARBY"

    assert unpack(navigate(server, target["address"])) == dict(
        reachable=True, distance_tiles=5, next_coord=[2, 3], movement_required=True,
        requested_target={"target_address": target["address"]},
        next_coord_role="FIRST_PATH_TILE_NOT_DESTINATION",
    )
    assert server.action is None
    assert agent.coord == (1, 3) and agent.spatial.tree == memory_before
    assert perceive(server) == perception


@pytest.mark.parametrize("vision,attention", [(4, 6), (6, 1), (6, 0)])
def test_unremembered_object_excluded_by_vision_or_attention_is_rejected(vision, attention):
    server, _ = navigation_server(vision=vision, attention=attention)
    perception = perceive(server)
    assert COUNTER not in [item["address"] for item in perception["game_objects"]]
    assert_unknown(navigate(server, COUNTER))


@pytest.mark.parametrize("object_address", [SHELF, COUNTER])
def test_current_or_remembered_arena_does_not_grant_unknown_object_knowledge(object_address):
    server, agent = navigation_server(vision=1)
    agent.spatial.tree[HOME[0]][HOME[1]]["厨房"] = []
    assert unpack(navigate(server, object_address[:3]))["reachable"]
    assert_unknown(navigate(server, object_address))


def test_remembered_object_remains_navigable_outside_perception():
    server, agent = navigation_server(vision=0, attention=0)
    agent.spatial.tree[HOME[0]][HOME[1]]["厨房"] = ["料理台"]
    assert COUNTER not in [item["address"] for item in perceive(server)["game_objects"]]
    assert unpack(navigate(server, COUNTER))["reachable"]
    assert server.action is None and agent.coord == (1, 3)


def test_observed_object_behind_blocked_door_returns_unreachable():
    server, agent = navigation_server(blocked_door=True)
    assert COUNTER in [item["address"] for item in perceive(server)["game_objects"]]
    assert unpack(navigate(server, COUNTER)) == dict(
        reachable=False, reason="BLOCKED_OR_DISCONNECTED",
    )
    assert server.action is None and agent.coord == (1, 3)


def test_same_object_name_at_another_address_is_not_disclosed():
    server, _ = navigation_server()
    perception = perceive(server)
    assert COUNTER[-1] == OTHER_COUNTER[-1]
    assert COUNTER in [item["address"] for item in perception["game_objects"]]
    assert OTHER_COUNTER not in [item["address"] for item in perception["game_objects"]]
    assert unpack(navigate(server, COUNTER))["reachable"]
    assert_unknown(navigate(server, OTHER_COUNTER))


def test_current_object_remains_available_with_zero_attention_and_vision():
    server, agent = navigation_server(vision=0, attention=0)
    assert [item["address"] for item in perceive(server)["game_objects"]] == [BED]
    assert unpack(navigate(server, BED)) == dict(
        reachable=True, distance_tiles=0, next_coord=[1, 3], movement_required=False,
        requested_target={"target_address": BED},
        next_coord_role="FIRST_PATH_TILE_NOT_DESTINATION",
    )
    assert server.action is None and agent.coord == (1, 3)
