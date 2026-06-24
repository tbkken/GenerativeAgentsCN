#!/usr/bin/env python3
"""Runnable scaffold for chapter 14: inspect the chapter 13 replay via Maze."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
ASSET_PATH = ROOT / "docs" / "book" / "assets" / "chapter_14" / "ch14_world_model_demo.png"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules.maze import Maze  # noqa: E402
from modules.memory.spatial import Spatial  # noqa: E402
from modules.utils.log import create_io_logger  # noqa: E402


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
    return ImageFont.load_default()


def find_first_active_frame(movement: dict) -> tuple[str, dict]:
    all_movement = movement["all_movement"]
    frame_keys = sorted(
        (key for key in all_movement.keys() if key.isdigit()),
        key=lambda key: int(key),
    )
    for key in frame_keys:
        frame = all_movement[key]
        if frame and any("action" in agent for agent in frame.values()):
            return key, frame
    raise RuntimeError("No active replay frame found in movement.json")


def draw_world_model_image(
    maze: Maze,
    agents: dict[str, dict],
    scope: list,
    same_arena_tiles: list,
    address_tiles: set[tuple[int, int]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    agent_coords = {tuple(agent["coord"]) for agent in agents.values()}
    scope_coords = {tile.coord for tile in scope}
    same_arena_coords = {tile.coord for tile in same_arena_tiles}
    focus = agent_coords | scope_coords | same_arena_coords | address_tiles

    min_x = max(min(x for x, _ in focus) - 3, 0)
    max_x = min(max(x for x, _ in focus) + 3, maze.maze_width - 1)
    min_y = max(min(y for _, y in focus) - 3, 0)
    max_y = min(max(y for _, y in focus) + 3, maze.maze_height - 1)

    cell = 18
    legend_h = 150
    grid_width = (max_x - min_x + 1) * cell
    width = max(grid_width, 800)
    height = (max_y - min_y + 1) * cell + legend_h
    image = Image.new("RGB", (width, height), "#f7f7f2")
    draw = ImageDraw.Draw(image)
    font = load_font(16)
    small_font = load_font(13)

    colors = {
        "empty": "#b6f28f",
        "address": "#e6d7a3",
        "collision": "#7f8175",
        "scope": "#fff2a6",
        "same_arena": "#9ee4ff",
        "target": "#f2b16d",
        "agent": "#d65cff",
        "grid": "#d8d8c8",
    }

    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            tile = maze.tile_at((x, y))
            fill = colors["empty"]
            if tile.collision:
                fill = colors["collision"]
            elif not tile.is_empty:
                fill = colors["address"]
            if (x, y) in scope_coords:
                fill = colors["scope"]
            if (x, y) in same_arena_coords:
                fill = colors["same_arena"]
            if (x, y) in address_tiles:
                fill = colors["target"]
            if (x, y) in agent_coords:
                fill = colors["agent"]

            px = (x - min_x) * cell
            py = (y - min_y) * cell
            draw.rectangle([px, py, px + cell - 1, py + cell - 1], fill=fill)
            draw.rectangle([px, py, px + cell - 1, py + cell - 1], outline=colors["grid"])

    agent_names = " / ".join(agents.keys())
    coord_text = ", ".join(str(tuple(agent["coord"])) for agent in agents.values())

    y0 = height - legend_h + 12
    draw.text((12, y0), "第 14 章脚手架：用 Maze 解读 book-config-ai-seminar 回放", fill="#202020", font=font)
    legend = [
        ("角色", colors["agent"], f"{agent_names} @ {coord_text}"),
        ("地址", colors["target"], f"图书馆桌子候选 tile {len(address_tiles)} 个"),
        ("视野", colors["scope"], f"默认 vision_r 视野 {len(scope)} 个 tile"),
        ("同场景", colors["same_arena"], f"同一 arena 可感知候选 {len(same_arena_tiles)} 个 tile"),
        ("阻挡", colors["collision"], "collision 阻挡移动"),
    ]
    positions = [(12, y0 + 38), (310, y0 + 38), (570, y0 + 38), (12, y0 + 80), (310, y0 + 80)]
    for (x, y), (label, color, text) in zip(positions, legend):
        draw.rectangle([x, y, x + 18, y + 18], fill=color, outline="#666666")
        draw.text((x + 24, y - 1), f"{label}: {text}", fill="#202020", font=small_font)

    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the chapter 13 replay through the world model.")
    parser.add_argument("--output", type=Path, default=ASSET_PATH, help="PNG output path.")
    args = parser.parse_args()

    maze_config_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "maze.json"
    movement_path = GENERATIVE_AGENTS / "results" / "compressed" / "book-config-ai-seminar" / "movement.json"
    data_config_path = GENERATIVE_AGENTS / "data" / "config.json"
    aisha_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "agents" / "阿伊莎" / "agent.json"

    maze_config = load_json(maze_config_path)
    movement = load_json(movement_path)
    data_config = load_json(data_config_path)
    aisha_config = load_json(aisha_path)

    maze = Maze(copy.deepcopy(maze_config), create_io_logger("error"))
    frame_key, frame = find_first_active_frame(movement)
    agents = {
        name: {
            "coord": tuple(data["movement"]),
            "location": data["location"],
            "action": data.get("action", ""),
        }
        for name, data in frame.items()
    }

    first_coord = next(iter(agents.values()))["coord"]
    tile = maze.tile_at(first_coord)
    address_tiles = maze.get_address_tiles(tile.address)
    percept_config = data_config["agent"]["percept"]
    scope = maze.get_scope(first_coord, percept_config)
    same_arena_tiles = [
        scope_tile
        for scope_tile in scope
        if scope_tile.has_address("arena")
        and tile.has_address("arena")
        and scope_tile.get_address("arena") == tile.get_address("arena")
    ]

    spatial_config = aisha_config["spatial"]
    spatial = Spatial(copy.deepcopy(spatial_config["tree"]), copy.deepcopy(spatial_config["address"]))
    sleep_address = spatial.find_address("准备睡觉", as_list=False)
    library_leaves = spatial.get_leaves(["the Ville", "奥克山学院", "图书馆"])

    draw_world_model_image(maze, agents, scope, same_arena_tiles, address_tiles, args.output)

    print("Chapter 14 world-model scaffold")
    print("=" * 38)
    print(f"source_replay: {movement_path.relative_to(ROOT)}")
    print(f"maze_json: {maze_config_path.relative_to(ROOT)}")
    print(f"data_config: {data_config_path.relative_to(ROOT)}")
    print(f"agent_json: {aisha_path.relative_to(ROOT)}")
    print(f"world: {maze_config['world']}")
    print(f"size: width={maze.maze_width}, height={maze.maze_height}, tile_size={maze.tile_size}")
    print(f"address_keys: {' -> '.join(maze_config['tile_address_keys'])}")
    print()
    print("Replay frame -> Maze tile")
    print(f"frame: {frame_key}")
    for name, data in agents.items():
        print(f"{name}:")
        print(f"  coord: {data['coord']}")
        print(f"  replay_location: {data['location']}")
        print(f"  replay_action: {data['action']}")
    print(f"tile_at_coord: coord[{first_coord[0]},{first_coord[1]}]")
    print(f"tile_address: {' -> '.join(tile.address)}")
    print(f"tile_collision: {tile.collision}")
    print(f"address_tile_count: {len(address_tiles)}")
    print(f"address_tiles: {sorted(address_tiles)}")
    print()
    print("Perception from this tile")
    print(
        "percept_config: "
        f"mode={percept_config['mode']}, "
        f"vision_r={percept_config['vision_r']}, "
        f"att_bandwidth={percept_config['att_bandwidth']}"
    )
    print(f"vision_scope_count: {len(scope)}")
    print(f"same_arena: {' -> '.join(tile.get_address('arena'))}")
    print(f"same_arena_tiles_in_scope: {len(same_arena_tiles)}")
    print()
    print("Spatial memory")
    print(f"阿伊莎.find_address('准备睡觉'): {sleep_address}")
    print(f"阿伊莎 known library leaves: {', '.join(library_leaves)}")
    print()
    print(f"image: {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
