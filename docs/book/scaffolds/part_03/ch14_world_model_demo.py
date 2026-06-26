#!/usr/bin/env python3
"""Runnable scaffold for chapter 14: inspect the chapter 13 replay via Maze."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# This scaffold lives under docs/book/scaffolds/part_03/.
# parents[4] walks back to the repository root, so the script can be run
# from the root with: python docs/book/scaffolds/part_03/ch14_world_model_demo.py
ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
ASSET_PATH = ROOT / "docs" / "book" / "assets" / "chapter_14" / "ch14_world_model_demo.png"
TILEMAP_DIR = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "tilemap"
TILEMAP_PATH = TILEMAP_DIR / "tilemap.json"

# Tiled stores flip flags in the high bits of a global tile id.
# The mask keeps only the real tile id before locating the tile image.
TILED_GID_MASK = 0x1FFFFFFF
TILED_FLIP_H = 0x80000000
TILED_FLIP_V = 0x40000000
TILED_FLIP_D = 0x20000000

# Follow the same visible map layers used by the frontend Phaser scene.
VISIBLE_TILEMAP_LAYERS = [
    "Bottom Ground",
    "Exterior Ground",
    "Exterior Decoration L1",
    "Exterior Decoration L2",
    "Interior Ground",
    "Wall",
    "Interior Furniture L1",
    "Interior Furniture L2 ",
    "Foreground L1",
    "Foreground L2",
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GENERATIVE_AGENTS))


def load_repo_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Maze only needs Event, and this chapter only needs Spatial. Loading the whole
# modules.memory package also imports vector-memory dependencies that are not
# needed for this world-model scaffold.
memory_pkg = types.ModuleType("modules.memory")
memory_pkg.__path__ = [str(GENERATIVE_AGENTS / "modules" / "memory")]
memory_pkg = sys.modules.setdefault("modules.memory", memory_pkg)
event_module = load_repo_module("modules.memory.event", GENERATIVE_AGENTS / "modules" / "memory" / "event.py")
spatial_module = load_repo_module("modules.memory.spatial", GENERATIVE_AGENTS / "modules" / "memory" / "spatial.py")
setattr(memory_pkg, "event", event_module)
setattr(memory_pkg, "spatial", spatial_module)

from modules.maze import Maze  # noqa: E402
from modules.utils.log import create_io_logger  # noqa: E402

Spatial = spatial_module.Spatial


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


def load_tilesets(tilemap_config: dict) -> list[dict]:
    tilesets = []
    for tileset in tilemap_config["tilesets"]:
        image_path = TILEMAP_DIR / Path(tileset["image"]).name
        if not image_path.exists():
            continue
        tilesets.append(
            {
                "firstgid": tileset["firstgid"],
                "columns": tileset["columns"],
                "tilewidth": tileset["tilewidth"],
                "tileheight": tileset["tileheight"],
                "image": Image.open(image_path).convert("RGBA"),
            }
        )
    return sorted(tilesets, key=lambda item: item["firstgid"])


def tileset_for_gid(tilesets: list[dict], gid: int) -> dict:
    selected = tilesets[0]
    for tileset in tilesets:
        if tileset["firstgid"] <= gid:
            selected = tileset
        else:
            break
    return selected


def render_tilemap_crop(tilemap_config: dict, min_x: int, min_y: int, max_x: int, max_y: int) -> Image.Image:
    tile_w = tilemap_config["tilewidth"]
    tile_h = tilemap_config["tileheight"]
    width_tiles = max_x - min_x + 1
    height_tiles = max_y - min_y + 1
    crop = Image.new("RGBA", (width_tiles * tile_w, height_tiles * tile_h), (0, 0, 0, 0))
    tilesets = load_tilesets(tilemap_config)

    # Rebuild the visual map crop from the real tilemap and tileset images.
    layers = {layer["name"]: layer for layer in tilemap_config["layers"]}
    for layer_name in VISIBLE_TILEMAP_LAYERS:
        layer = layers.get(layer_name)
        if not layer or not layer.get("visible", True):
            continue
        data = layer.get("data", [])
        opacity = layer.get("opacity", 1)
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                raw_gid = data[y * tilemap_config["width"] + x]
                gid = raw_gid & TILED_GID_MASK
                if gid == 0:
                    continue

                tileset = tileset_for_gid(tilesets, gid)
                local_id = gid - tileset["firstgid"]
                sx = (local_id % tileset["columns"]) * tile_w
                sy = (local_id // tileset["columns"]) * tile_h
                tile = tileset["image"].crop((sx, sy, sx + tile_w, sy + tile_h))

                if raw_gid & TILED_FLIP_D:
                    tile = tile.transpose(Image.Transpose.TRANSPOSE)
                if raw_gid & TILED_FLIP_H:
                    tile = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if raw_gid & TILED_FLIP_V:
                    tile = tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                if opacity < 1:
                    alpha = tile.getchannel("A").point(lambda value: int(value * opacity))
                    tile.putalpha(alpha)

                crop.alpha_composite(tile, ((x - min_x) * tile_w, (y - min_y) * tile_h))

    return crop


def find_first_active_frame(movement: dict) -> tuple[str, dict]:
    # Skip empty frames and use the first frame that contains agent actions.
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

    # The base image is the real frontend town map. The overlays are the
    # backend world-model probes: scope, arena, address candidates and agents.
    tilemap_config = load_json(TILEMAP_PATH)
    agent_coords = {tuple(agent["coord"]) for agent in agents.values()}
    scope_coords = {tile.coord for tile in scope}
    same_arena_coords = {tile.coord for tile in same_arena_tiles}
    focus = agent_coords | scope_coords | same_arena_coords | address_tiles

    min_x = max(min(x for x, _ in focus) - 3, 0)
    max_x = min(max(x for x, _ in focus) + 3, maze.maze_width - 1)
    min_y = max(min(y for _, y in focus) - 3, 0)
    max_y = min(max(y for _, y in focus) + 3, maze.maze_height - 1)

    map_crop = render_tilemap_crop(tilemap_config, min_x, min_y, max_x, max_y)
    tile_w = tilemap_config["tilewidth"]
    tile_h = tilemap_config["tileheight"]
    legend_h = 210
    padding_x = 12
    width = max(map_crop.width + padding_x * 2, 980)
    map_x = (width - map_crop.width) // 2
    height = map_crop.height + legend_h
    image = Image.new("RGBA", (width, height), "#f7f7f2")
    image.alpha_composite(map_crop, (map_x, 0))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(16)
    small_font = load_font(13)

    colors = {
        "scope": (255, 226, 80, 42),
        "same_arena": (64, 190, 255, 76),
        "address": (255, 139, 43, 135),
        "address_outline": (180, 86, 0, 230),
        "agent": (198, 58, 255, 230),
        "agent_outline": (255, 255, 255, 255),
        "collision": (50, 50, 50, 70),
    }

    def tile_rect(coord: tuple[int, int], inset: int = 0) -> list[int]:
        x, y = coord
        px = map_x + (x - min_x) * tile_w + inset
        py = (y - min_y) * tile_h + inset
        return [px, py, px + tile_w - 1 - inset * 2, py + tile_h - 1 - inset * 2]

    for coord in scope_coords:
        if min_x <= coord[0] <= max_x and min_y <= coord[1] <= max_y:
            draw.rectangle(tile_rect(coord), fill=colors["scope"])
    for coord in same_arena_coords:
        if min_x <= coord[0] <= max_x and min_y <= coord[1] <= max_y:
            draw.rectangle(tile_rect(coord), fill=colors["same_arena"])
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if maze.tile_at((x, y)).collision:
                draw.rectangle(tile_rect((x, y), 1), outline=colors["collision"], width=2)
    for coord in address_tiles:
        if min_x <= coord[0] <= max_x and min_y <= coord[1] <= max_y:
            draw.rectangle(tile_rect(coord, 2), fill=colors["address"], outline=colors["address_outline"], width=2)
    for coord in agent_coords:
        rect = tile_rect(coord, 5)
        draw.ellipse(rect, fill=colors["agent"], outline=colors["agent_outline"], width=3)

    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    agent_names = " / ".join(agents.keys())
    coord_text = ", ".join(str(tuple(agent["coord"])) for agent in agents.values())

    y0 = map_crop.height + 12
    draw.text((12, y0), "第 14 章脚手架：用世界地图 Maze 解读 book-config-ai-seminar 回放", fill="#202020", font=font)
    legend = [
        ("角色", colors["agent"], f"{agent_names} @ {coord_text}"),
        ("语义地址 address", colors["address"], f"图书馆桌子候选地图格子 Tile {len(address_tiles)} 个"),
        ("视野范围 vision", colors["scope"], f"默认感知范围 {len(scope)} 个地图格子 Tile"),
        ("场所 arena", colors["same_arena"], f"同一场所可感知候选 {len(same_arena_tiles)} 个地图格子 Tile"),
        ("碰撞标记 collision", colors["collision"], "不可通行地图格子轮廓"),
    ]
    positions = [
        (12, y0 + 38),
        (12, y0 + 68),
        (12, y0 + 98),
        (12, y0 + 128),
        (12, y0 + 158),
    ]
    for (x, y), (label, color, text) in zip(positions, legend):
        draw.rectangle([x, y, x + 18, y + 18], fill=color, outline="#666666")
        draw.text((x + 24, y - 1), f"{label}: {text}", fill="#202020", font=small_font)

    image.convert("RGB").save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the chapter 13 replay through the world model.")
    parser.add_argument("--output", type=Path, default=ASSET_PATH, help="PNG output path.")
    args = parser.parse_args()

    # Four inputs connect chapter 13's replay to chapter 14's source reading.
    maze_config_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "maze.json"
    movement_path = GENERATIVE_AGENTS / "results" / "compressed" / "book-config-ai-seminar" / "movement.json"
    data_config_path = GENERATIVE_AGENTS / "data" / "config.json"
    aisha_path = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "agents" / "阿伊莎" / "agent.json"

    maze_config = load_json(maze_config_path)
    movement = load_json(movement_path)
    data_config = load_json(data_config_path)
    aisha_config = load_json(aisha_path)

    # 1. Build the backend world model from maze.json.
    maze = Maze(copy.deepcopy(maze_config), create_io_logger("error"))

    # 2. Read the first active replay frame from chapter 13's movement.json.
    frame_key, frame = find_first_active_frame(movement)
    agents = {
        name: {
            "coord": tuple(data["movement"]),
            "location": data["location"],
            "action": data.get("action", ""),
        }
        for name, data in frame.items()
    }

    # 3. Interpret the replay coordinate through Maze and Tile.
    first_coord = next(iter(agents.values()))["coord"]
    tile = maze.tile_at(first_coord)
    address_tiles = maze.get_address_tiles(tile.address)

    # 4. Apply the real percept config, then keep only tiles in the same arena.
    percept_config = data_config["agent"]["percept"]
    scope = maze.get_scope(first_coord, percept_config)
    same_arena_tiles = [
        scope_tile
        for scope_tile in scope
        if scope_tile.has_address("arena")
        and tile.has_address("arena")
        and scope_tile.get_address("arena") == tile.get_address("arena")
    ]

    # 5. Inspect Aisha's subjective spatial memory.
    spatial_config = aisha_config["spatial"]
    spatial = Spatial(copy.deepcopy(spatial_config["tree"]), copy.deepcopy(spatial_config["address"]))
    sleep_address = spatial.find_address("准备睡觉", as_list=False)
    library_leaves = spatial.get_leaves(["the Ville", "奥克山学院", "图书馆"])

    # 6. Render the same evidence as a real-town-map figure for the book.
    draw_world_model_image(maze, agents, scope, same_arena_tiles, address_tiles, args.output)

    print("第 14 章世界模型脚手架")
    print("=" * 38)
    print(f"source_replay: {movement_path.relative_to(ROOT)}")
    print(f"maze_json: {maze_config_path.relative_to(ROOT)}")
    print(f"data_config: {data_config_path.relative_to(ROOT)}")
    print(f"agent_json: {aisha_path.relative_to(ROOT)}")
    print(f"world: {maze_config['world']}")
    print(f"size: width={maze.maze_width}, height={maze.maze_height}, tile_size={maze.tile_size}")
    print(f"address_keys: {' -> '.join(maze_config['tile_address_keys'])}")
    print()
    print("回放帧 -> 世界地图 Maze / 地图格子 Tile")
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
    print("从当前地图格子 Tile 计算感知")
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
    print("空间记忆 Spatial")
    print(f"阿伊莎.find_address('准备睡觉'): {sleep_address}")
    print(f"阿伊莎已知图书馆对象: {', '.join(library_leaves)}")
    print()
    print(f"image: {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
