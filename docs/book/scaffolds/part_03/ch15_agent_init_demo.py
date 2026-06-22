#!/usr/bin/env python3
"""Runnable scaffold for chapter 15: agent initialization."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules import utils  # noqa: E402
from modules.agent import Agent  # noqa: E402
from modules.maze import Maze  # noqa: E402


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    utils.set_timer("20240214-08:00")

    config_path = GENERATIVE_AGENTS / "data" / "config.json"
    agent_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "agents" / "伊莎贝拉" / "agent.json"
    maze_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "maze.json"

    runtime_config = load_json(config_path)["agent"]
    agent_seed = load_json(agent_path)

    agent_base = copy.deepcopy(runtime_config)
    agent_base["associate"]["embedding"] = {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434",
    }
    agent_config = utils.update_dict(copy.deepcopy(agent_base), copy.deepcopy(agent_seed))
    agent_config["storage_root"] = f"/private/tmp/ch15_agent_init_{os.getpid()}"

    maze = Maze(load_json(maze_path), utils.create_io_logger("error"))
    agent = Agent(agent_config, maze, {}, utils.create_io_logger("error"))
    agent.scratch.template_path = str(GENERATIVE_AGENTS / "data" / "prompts")
    tile = agent.get_tile()
    action = agent.action.abstract()
    base_desc = agent.scratch._base_desc()

    print("Chapter 15 agent-initialization scaffold")
    print("=" * 46)
    print(f"agent_config: {agent_path.relative_to(ROOT)}")
    print(f"runtime_config: {config_path.relative_to(ROOT)}")
    print()
    print("Loaded identity")
    print(f"name: {agent.name}")
    print(f"coord: {agent.coord}")
    print(f"tile_address: {tile.get_address(as_list=False)}")
    print(f"currently: {agent.scratch.currently}")
    print()
    print("Scratch fields")
    for key in ["age", "innate", "learned", "lifestyle", "daily_plan"]:
        print(f"{key}: {agent.scratch.config[key]}")
    print()
    print("Spatial memory")
    print(f"living_area: {' -> '.join(agent.spatial.address['living_area'])}")
    print(f"sleep_address: {' -> '.join(agent.spatial.address['睡觉'])}")
    print(f"known_world_roots: {', '.join(agent.spatial.tree.keys())}")
    print()
    print("Runtime modules")
    print(f"percept_config: {agent.percept_config}")
    print(f"think_provider: {agent.think_config['llm']['provider']}")
    print(f"associate_nodes: {agent.associate.index.nodes_num}")
    print(f"schedule_created: {agent.schedule.create}")
    print(f"schedule_items: {len(agent.schedule.daily_schedule)}")
    print()
    print("Initial action")
    print(f"event: {action['event']}")
    print(f"object: {action.get('object')}")
    print()
    print("Base prompt preview")
    for line in base_desc.splitlines()[:8]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
