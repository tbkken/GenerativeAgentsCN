#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the chapter 16 simulation-step overview figure from real project assets."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
STATIC_ROOT = GENERATIVE_AGENTS / "frontend" / "static"
TILEMAP_DIR = STATIC_ROOT / "assets" / "village" / "tilemap"
TILEMAP_PATH = TILEMAP_DIR / "tilemap.json"
DEMO_PATH = ROOT / "docs" / "book" / "scaffolds" / "part_03" / "ch16_simulation_loop_demo.py"
ASSET_PATH = ROOT / "docs" / "book" / "assets" / "chapter_16" / "ch16_simulation_step_overview.png"

TILED_GID_MASK = 0x1FFFFFFF
TILED_FLIP_H = 0x80000000
TILED_FLIP_V = 0x40000000
TILED_FLIP_D = 0x20000000

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

AGENT_COLORS = {
    "克劳斯": "#7c3aed",
    "玛丽亚": "#0f766e",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
    return ImageFont.load_default()


def import_demo_module():
    spec = importlib.util.spec_from_file_location("ch16_simulation_loop_demo", DEMO_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {DEMO_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def collect_step_data():
    demo = import_demo_module()
    demo.random.seed(16)
    demo.utils.set_timer(demo.START_TIME)
    config = demo.build_config()
    logger = demo.utils.create_io_logger("error")
    conversation: dict = {}
    storage_root = Path(tempfile.mkdtemp(prefix="ch16_loop_figure_"))

    original_completion = demo.Agent.completion
    original_add_concept = demo.Agent._add_concept
    original_reaction = demo.Agent._reaction

    try:
        demo.Agent.completion = demo.stub_completion
        demo.Agent._add_concept = demo.stub_add_concept
        demo.Agent._reaction = lambda self, agents=None, ignore_words=None: False

        maze = demo.Maze(load_json(demo.STATIC_ROOT / config["maze"]["path"]), logger)
        agents = {
            name: demo.build_agent(name, config["agent_base"], maze, conversation, logger, storage_root)
            for name in demo.SELECTED_AGENTS
        }

        game = demo.Game.__new__(demo.Game)
        game.name = "ch16-loop-figure"
        game.static_root = str(demo.STATIC_ROOT)
        game.record_iterval = 30
        game.logger = logger
        game.maze = maze
        game.conversation = conversation
        game.agents = agents

        agent_status = {
            name: {"coord": list(agent.coord), "path": []}
            for name, agent in agents.items()
        }

        timer = demo.utils.get_timer()
        before_time = timer.get_date("%Y%m%d-%H:%M")
        records = []
        for index, (name, status) in enumerate(agent_status.items(), start=1):
            before_coord = list(status["coord"])
            before_address = maze.tile_at(before_coord).get_address(as_list=False)
            result = demo.Game.agent_think(game, name, status)
            plan = result["plan"]
            info = result["info"]
            agent = game.get_agent(name)
            path = [tuple(coord) for coord in plan.get("path", [])]
            if path:
                status["coord"], status["path"] = list(path[-1]), []
            records.append(
                {
                    "index": index,
                    "name": name,
                    "before_coord": tuple(before_coord),
                    "before_address": before_address,
                    "completion_calls": list(agent._ch16_completion_calls),
                    "action_after": demo.action_text(agent),
                    "path": path,
                    "path_len": len(path),
                    "path_target": path[-1] if path else tuple(before_coord),
                    "status_after": tuple(status["coord"]),
                    "record_flag": info["record"],
                }
            )

        timer.forward(demo.STRIDE_MINUTES)
        return {
            "maze": maze,
            "records": records,
            "before_time": before_time,
            "after_time": timer.get_date("%Y%m%d-%H:%M"),
            "stride": demo.STRIDE_MINUTES,
            "selected_agents": list(demo.SELECTED_AGENTS),
        }
    finally:
        demo.Agent.completion = original_completion
        demo.Agent._add_concept = original_add_concept
        demo.Agent._reaction = original_reaction
        shutil.rmtree(storage_root, ignore_errors=True)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if text_size(draw, candidate, font)[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int = 6,
) -> int:
    x, y = xy
    for line in wrap_text(draw, text, font, max_width):
        draw.text((x, y), line, fill=fill, font=font)
        y += text_size(draw, line or " ", font)[1] + line_gap
    return y


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: str, font: ImageFont.ImageFont) -> None:
    x, y = xy
    w, h = text_size(draw, text, font)
    draw.rounded_rectangle([x, y, x + w + 16, y + h + 10], radius=6, fill="#ffffff", outline=color, width=2)
    draw.text((x + 8, y + 5), text, fill=color, font=font)


def marker(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, fill: str, text: str, font: ImageFont.ImageFont) -> None:
    x, y = center
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill, outline="#ffffff", width=4)
    tw, th = text_size(draw, text, font)
    draw.text((x - tw // 2, y - th // 2 - 1), text, fill="#ffffff", font=font)


def agent_portrait(name: str, size: int = 62) -> Image.Image:
    portrait_path = STATIC_ROOT / "assets" / "village" / "agents" / name / "portrait.png"
    portrait = Image.open(portrait_path).convert("RGBA").resize((size, size), Image.Resampling.NEAREST)
    frame = Image.new("RGBA", (size + 12, size + 12), (0, 0, 0, 0))
    mask = Image.new("L", frame.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size + 11, size + 11], radius=8, fill=255)
    bg = Image.new("RGBA", frame.size, "#f9f5ec")
    bg.putalpha(mask)
    frame.alpha_composite(bg)
    frame.alpha_composite(portrait, (6, 6))
    return frame


def draw_map_panel(base: Image.Image, xy: tuple[int, int], size: tuple[int, int], data: dict, fonts: dict) -> None:
    image = base
    draw = ImageDraw.Draw(image)
    x0, y0 = xy
    panel_w, panel_h = size
    records = data["records"]
    maze = data["maze"]
    tilemap_config = load_json(TILEMAP_PATH)

    all_coords = set()
    for record in records:
        all_coords.add(record["before_coord"])
        all_coords.add(record["path_target"])
        all_coords.update(record["path"])
    min_x = max(min(x for x, _ in all_coords) - 4, 0)
    max_x = min(max(x for x, _ in all_coords) + 4, maze.maze_width - 1)
    min_y = max(min(y for _, y in all_coords) - 4, 0)
    max_y = min(max(y for _, y in all_coords) + 4, maze.maze_height - 1)

    crop = render_tilemap_crop(tilemap_config, min_x, min_y, max_x, max_y)
    tile_w = tilemap_config["tilewidth"]
    tile_h = tilemap_config["tileheight"]

    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(overlay)

    def center(coord: tuple[int, int]) -> tuple[int, int]:
        x, y = coord
        return ((x - min_x) * tile_w + tile_w // 2, (y - min_y) * tile_h + tile_h // 2)

    for record in records:
        color = AGENT_COLORS[record["name"]]
        points = [center(coord) for coord in record["path"]]
        if len(points) >= 2:
            o_draw.line(points, fill=color + "d8", width=8, joint="curve")
            for point in points[::8]:
                o_draw.ellipse([point[0] - 4, point[1] - 4, point[0] + 4, point[1] + 4], fill="#ffffffcc")
        start = center(record["before_coord"])
        end = center(record["path_target"])
        marker(o_draw, start, 18, color, f"{record['index']}起", fonts["tiny_bold"])
        marker(o_draw, end, 18, color, f"{record['index']}终", fonts["tiny_bold"])

    map_image = Image.alpha_composite(crop, overlay)
    scale = min((panel_w - 36) / map_image.width, (panel_h - 142) / map_image.height)
    scaled = map_image.resize((int(map_image.width * scale), int(map_image.height * scale)), Image.Resampling.BICUBIC)

    draw.rounded_rectangle([x0, y0, x0 + panel_w, y0 + panel_h], radius=8, fill="#fbfaf6", outline="#d8d0bd", width=2)
    draw.text((x0 + 18, y0 + 16), "真实小镇地图：一次仿真步 step 的移动结果", fill="#242424", font=fonts["h2"])
    draw.text((x0 + 18, y0 + 52), "路径来自世界地图 Maze 的地址落地与寻路，不是手绘示意", fill="#63615c", font=fonts["small"])

    map_x = x0 + (panel_w - scaled.width) // 2
    map_y = y0 + 86
    image.alpha_composite(scaled, (map_x, map_y))

    legend_y = y0 + panel_h - 44
    lx = x0 + 20
    for record in records:
        color = AGENT_COLORS[record["name"]]
        draw.line([lx, legend_y + 10, lx + 34, legend_y + 10], fill=color, width=6)
        draw.text((lx + 44, legend_y), f"{record['index']} {record['name']}：{record['before_coord']} -> {record['path_target']}", fill="#282828", font=fonts["small"])
        lx += 330


def draw_agent_card(base: Image.Image, xy: tuple[int, int], width: int, record: dict, fonts: dict) -> int:
    draw = ImageDraw.Draw(base)
    x, y = xy
    color = AGENT_COLORS[record["name"]]
    card_h = 330
    draw.rounded_rectangle([x, y, x + width, y + card_h], radius=8, fill="#ffffff", outline="#ddd6c5", width=2)
    draw.rounded_rectangle([x, y, x + width, y + 72], radius=8, fill=color)
    draw.rectangle([x, y + 52, x + width, y + 72], fill=color)
    portrait = agent_portrait(record["name"])
    base.alpha_composite(portrait, (x + 18, y + 16))
    draw.text((x + 100, y + 15), f"[{record['index']}] {record['name']}", fill="#ffffff", font=fonts["h2"])
    draw.text((x + 100, y + 46), f"输入坐标 coord {record['before_coord']} -> 服务端坐标 {record['status_after']}", fill="#f7f4ef", font=fonts["small"])

    left = x + 24
    max_text = width - 48
    cursor = y + 92
    sections = [
        ("输入状态 status_before", f"地址 address：{record['before_address']}"),
        ("提示词调用 completion_calls", " -> ".join(record["completion_calls"])),
        ("行动结果 action_after", record["action_after"]),
        ("返回路径 returned_path", f"长度 {record['path_len']}，终点 {record['path_target']}，记录标记 record_flag={record['record_flag']}"),
    ]
    for title, body in sections:
        draw.text((left, cursor), title, fill=color, font=fonts["small_bold"])
        cursor += 24
        cursor = draw_wrapped(draw, (left, cursor), body, fonts["small"], "#2f2f2f", max_text, line_gap=5)
        cursor += 10
    return y + card_h


def draw_files_band(base: Image.Image, xy: tuple[int, int], size: tuple[int, int], data: dict, fonts: dict) -> None:
    draw = ImageDraw.Draw(base)
    x, y = xy
    w, h = size
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill="#f2f7fb", outline="#bfd4e6", width=2)
    draw.text((x + 18, y + 18), "本仿真步 step 的落盘与时间推进", fill="#1d3a52", font=fonts["h2"])
    items = [
        ("断点 checkpoint", f"simulate-{data['before_time'].replace(':', '')}.json"),
        ("对话 conversation", "conversation.json"),
        ("时间推进 timer.forward()", f"{data['before_time']} -> {data['after_time']}"),
    ]
    col_w = (w - 52) // 3
    for i, (title, body) in enumerate(items):
        ix = x + 18 + i * col_w
        iy = y + 64
        draw.rounded_rectangle([ix, iy, ix + col_w - 14, iy + 76], radius=8, fill="#ffffff", outline="#d5e5f0")
        draw.text((ix + 14, iy + 12), title, fill="#2e5b78", font=fonts["small_bold"])
        draw.text((ix + 14, iy + 42), body, fill="#2a2a2a", font=fonts["small"])


def draw_figure(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1800, 1080
    image = Image.new("RGBA", (width, height), "#f4f1e8")
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": load_font(34, bold=True),
        "h2": load_font(24, bold=True),
        "small_bold": load_font(17, bold=True),
        "small": load_font(17),
        "tiny_bold": load_font(13, bold=True),
    }

    draw.text((48, 34), "图 16-2：一个仿真步 step 如何推进两个智能体 Agent", fill="#202020", font=fonts["title"])
    draw.text((50, 82), "材料来自真实小镇地图、真实角色配置和第 16 章脚手架输出；示例时间 20240213-09:30 -> 09:40。", fill="#62605a", font=fonts["small"])

    draw_map_panel(image, (48, 130), (760, 860), data, fonts)
    right_x = 844
    top = 130
    y = top
    for record in data["records"]:
        y = draw_agent_card(image, (right_x, y), 900, record, fonts) + 26
    draw_files_band(image, (right_x, y), (900, 146), data, fonts)

    draw.text(
        (50, height - 38),
        "数据来源：后端地图 maze.json、瓦片地图 tilemap.json、克劳斯/玛丽亚角色配置 agent.json 与 ch16_simulation_loop_demo.py",
        fill="#77736a",
        font=fonts["small"],
    )
    image.convert("RGB").save(output, quality=95)


def main() -> int:
    data = collect_step_data()
    draw_figure(data, ASSET_PATH)
    print(f"已生成第 16 章仿真步总览图: {ASSET_PATH.relative_to(ROOT)}")
    for record in data["records"]:
        print(f"{record['name']}: {record['before_coord']} -> {record['path_target']} path_len={record['path_len']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
