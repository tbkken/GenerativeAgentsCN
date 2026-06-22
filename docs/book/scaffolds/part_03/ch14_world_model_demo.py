#!/usr/bin/env python3
"""Runnable scaffold for chapter 14: world model, Tile, Maze, Spatial."""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
ASSET_PATH = ROOT / "docs" / "book" / "assets" / "chapter_14" / "ch14_world_model_demo.png"

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules.maze import Maze  # noqa: E402
from modules.memory.event import Event  # noqa: E402
from modules.memory.spatial import Spatial  # noqa: E402
from modules.utils.log import create_io_logger  # noqa: E402


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def choose_nearest(src: tuple[int, int], coords: set[tuple[int, int]]) -> tuple[int, int]:
    return min(coords, key=lambda c: (manhattan(src, c), c[1], c[0]))


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
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


def draw_world_model_image(
    maze: Maze,
    src: tuple[int, int],
    dst: tuple[int, int],
    path: list[tuple[int, int]],
    scope: list,
    target_tiles: set[tuple[int, int]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    scope_coords = {tile.coord for tile in scope}
    focus = set(path) | scope_coords | target_tiles | {src, dst}
    min_x = max(min(x for x, _ in focus) - 10, 0)
    max_x = min(max(x for x, _ in focus) + 10, maze.maze_width - 1)
    min_y = max(min(y for _, y in focus) - 8, 0)
    max_y = min(max(y for _, y in focus) + 8, maze.maze_height - 1)

    cell = 18
    legend_h = 126
    grid_width = (max_x - min_x + 1) * cell
    width = max(grid_width, 920)
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
        "target": "#f2b16d",
        "path": "#63a4ff",
        "src": "#39b54a",
        "dst": "#e94b5f",
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
            if (x, y) in target_tiles:
                fill = colors["target"]
            if (x, y) in path:
                fill = colors["path"]
            if (x, y) == src:
                fill = colors["src"]
            if (x, y) == dst:
                fill = colors["dst"]

            px = (x - min_x) * cell
            py = (y - min_y) * cell
            draw.rectangle([px, py, px + cell - 1, py + cell - 1], fill=fill)
            draw.rectangle([px, py, px + cell - 1, py + cell - 1], outline=colors["grid"])

    y0 = height - legend_h + 12
    draw.text((12, y0), "Chapter 14 scaffold: Maze path + vision scope + address tiles", fill="#202020", font=font)
    legend = [
        ("SRC", colors["src"], f"伊莎贝拉初始坐标 {src}"),
        ("DST", colors["dst"], f"目标对象坐标 {dst}"),
        ("PATH", colors["path"], f"寻路结果 {len(path)} 个 tile"),
        ("SCOPE", colors["scope"], f"vision_r=4 可见范围 {len(scope)} 个 tile"),
        ("ADDR", colors["target"], f"同一地址候选 tile {len(target_tiles)} 个"),
    ]
    positions = [(12, y0 + 36), (312, y0 + 36), (612, y0 + 36), (12, y0 + 72), (312, y0 + 72)]
    for (x, y), (label, color, text) in zip(positions, legend):
        draw.rectangle([x, y, x + 18, y + 18], fill=color, outline="#666666")
        draw.text((x + 24, y - 1), f"{label}: {text}", fill="#202020", font=small_font)

    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the chapter 14 world-model scaffold.")
    parser.add_argument("--output", type=Path, default=ASSET_PATH, help="PNG output path.")
    args = parser.parse_args()

    random.seed(7)

    maze_config_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "maze.json"
    agent_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "agents" / "伊莎贝拉" / "agent.json"
    maze_config = load_json(maze_config_path)
    agent_config = load_json(agent_path)

    maze = Maze(copy.deepcopy(maze_config), create_io_logger("error"))
    src = tuple(agent_config["coord"])
    target_address = ["the Ville", "霍布斯咖啡馆", "咖啡馆", "咖啡馆顾客座位"]
    target_tiles = maze.get_address_tiles(target_address)
    dst = choose_nearest(src, target_tiles)
    path = maze.find_path(src, dst)
    tile = maze.tile_at(dst)
    tile.add_event(
        Event(
            "伊莎贝拉",
            describe="伊莎贝拉此时检查咖啡机和研磨设备",
            address=target_address,
            emoji="☕",
        )
    )
    scope = maze.get_scope(dst, {"mode": "box", "vision_r": 4})

    spatial_config = agent_config["spatial"]
    spatial = Spatial(copy.deepcopy(spatial_config["tree"]), copy.deepcopy(spatial_config["address"]))
    sleep_address = spatial.find_address("准备睡觉", as_list=False)
    spatial.add_leaf(["the Ville", "霍布斯咖啡馆", "咖啡馆", "钢琴"])
    cafe_leaves = spatial.get_leaves(["the Ville", "霍布斯咖啡馆", "咖啡馆"])

    draw_world_model_image(maze, src, dst, path, scope, target_tiles, args.output)

    print("Chapter 14 world-model scaffold")
    print("=" * 38)
    print(f"maze_json: {maze_config_path.relative_to(ROOT)}")
    print(f"world: {maze_config['world']}")
    print(f"size: width={maze.maze_width}, height={maze.maze_height}, tile_size={maze.tile_size}")
    print(f"address_keys: {' -> '.join(maze_config['tile_address_keys'])}")
    print()
    print("Tile and address")
    print(f"target_address: {' -> '.join(target_address)}")
    print(f"address_tile_count: {len(target_tiles)}")
    print(f"chosen_tile: {dst}")
    print(f"tile_address: {' -> '.join(tile.address)}")
    print(f"tile_collision: {tile.collision}")
    print("tile_events:")
    for event in tile.get_events():
        print(f"  - {event}")
    print()
    print("Path and perception")
    print(f"src_coord: {src}")
    print(f"path_length: {len(path)}")
    print(f"path_preview: {path[:8]}{' ...' if len(path) > 8 else ''}")
    print(f"vision_scope_count: {len(scope)}")
    same_arena = [t for t in scope if t.has_address('arena') and t.get_address('arena') == tile.get_address('arena')]
    print(f"same_arena_tiles_in_scope: {len(same_arena)}")
    print()
    print("Spatial memory")
    print(f"find_address('准备睡觉'): {sleep_address}")
    print(f"after add_leaf cafe leaves: {', '.join(cafe_leaves)}")
    print()
    print(f"image: {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
