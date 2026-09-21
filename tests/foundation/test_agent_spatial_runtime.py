"""基础能力回归测试：覆盖 ``test_agent_spatial_runtime`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import logging
import random
from types import SimpleNamespace

import pytest

from generative_agents.ga_runtime.engine.actor import ActorState as Agent
from generative_agents.ga_runtime.engine.space import Maze
from generative_agents.ga_runtime.engine.space import MazeAddressNotFoundError








def test_maze_never_falls_back_to_an_unrelated_random_address():
    """回归验证 ``test_maze_never_falls_back_to_an_unrelated_random_address`` 所描述的业务结果、故障边界和隔离约束。"""
    maze = Maze(
        {
            "world": "test",
            "size": [1, 1],
            "tile_size": 16,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [
                {
                    "coord": [0, 0],
                    "collision": False,
                    "address": ["home", "bedroom", "bed"],
                }
            ],
        },
        logging.getLogger("test-maze-spatial"),
        random.Random(1),
    )

    with pytest.raises(MazeAddressNotFoundError) as caught:
        maze.get_address_tiles(["test", "unknown", "room", "object"])

    assert caught.value.code == "AGENT_SPATIAL_MAP_ADDRESS_INVALID"


def test_maze_pathfinding_uses_the_full_zero_based_tile_grid():
    """边缘 Tile 是合法空间；碰撞检测也必须使用同一格坐标合同。"""
    maze = Maze(
        {
            "world": "tile-world",
            "size": [3, 3],
            "size_unit": "TILE",
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "object"],
            "tiles": [
                {
                    "coord": [x, y],
                    "collision": (x, y) == (1, 0),
                    "address": [],
                }
                for y in range(3)
                for x in range(3)
            ],
        },
        logging.getLogger("test-maze-tile-grid"),
        random.Random(1),
    )

    assert maze.find_path((0, 0), (0, 0)) == [(0, 0)]
    assert maze.find_path((0, 0), (2, 0)) == [
        (0, 0),
        (0, 1),
        (1, 1),
        (2, 1),
        (2, 0),
    ]
    assert maze.find_path((0, 0), (1, 0)) == []
    assert maze.find_path((-1, 0), (0, 0)) == []


def test_system_map_object_level_is_available_to_legacy_game_object_runtime():
    """回归验证 ``test_system_map_object_level_is_available_to_legacy_game_object_runtime`` 所描述的业务结果、故障边界和隔离约束。"""
    maze = Maze(
        {
            "world": "system-map",
            "size": [1, 1],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "object"],
            "tiles": [
                {
                    "coord": [0, 0],
                    "collision": False,
                    "address": [
                        "system-map",
                        "street",
                        "crosswalk",
                        "traffic-light",
                    ],
                }
            ],
        },
        logging.getLogger("test-system-map-object-alias"),
        random.Random(1),
    )

    tile = maze.tile_at((0, 0))
    assert tile.address == [
        "system-map",
        "street",
        "crosswalk",
        "traffic-light",
    ]
    assert tile.has_address("object")
    assert tile.has_address("game_object")
    assert tile.get_address("object") == [
        "system-map",
        "street",
        "crosswalk",
        "traffic-light",
    ]
    assert tile.get_address("game_object") == tile.get_address("object")


def test_editor_partial_address_does_not_duplicate_world_root():
    """回归验证 ``test_editor_partial_address_does_not_duplicate_world_root`` 所描述的业务结果、故障边界和隔离约束。"""
    maze = Maze(
        {
            "world": "system-map",
            "size": [1, 1],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "object"],
            "tiles": [
                {
                    "coord": [0, 0],
                    "collision": False,
                    "address": ["system-map", "street", "crosswalk"],
                }
            ],
        },
        logging.getLogger("test-system-map-partial-address"),
        random.Random(1),
    )

    assert maze.tile_at((0, 0)).address == ["system-map", "street", "crosswalk"]
