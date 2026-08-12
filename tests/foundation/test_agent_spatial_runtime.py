from __future__ import annotations

import logging
import random
from types import SimpleNamespace

import pytest

from generative_agents.modules.agent import Agent, AgentSpatialConfigurationError
from generative_agents.modules.maze import Maze, MazeAddressNotFoundError


def _agent_with_address(address):
    agent = Agent.__new__(Agent)
    agent.name = "Runtime Agent"
    agent.spatial = SimpleNamespace(
        find_address=lambda _hint, as_list=True: list(address)
    )
    agent.maze = SimpleNamespace(
        address_tiles={"test:home:bedroom:bed": {(0, 0)}}
    )
    return agent


def test_required_spatial_address_rejects_legacy_empty_configuration():
    agent = _agent_with_address([])

    with pytest.raises(AgentSpatialConfigurationError) as caught:
        agent._required_spatial_address("sleeping")

    assert caught.value.code == "AGENT_SPATIAL_CONFIGURATION_INVALID"
    assert "Runtime Agent" in str(caught.value)


def test_required_spatial_address_rejects_address_from_another_map():
    agent = _agent_with_address(["test", "other", "bedroom", "bed"])

    with pytest.raises(AgentSpatialConfigurationError, match="当前地图"):
        agent._required_spatial_address("sleeping")


def test_maze_never_falls_back_to_an_unrelated_random_address():
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
