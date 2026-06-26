#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate experiential evidence figures for chapters 17-23.

These figures are not flowcharts. They turn local project artifacts into visual
evidence: real town-map crops, real replay positions, real JSON/config snippets,
real prompt files, and real trace values.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GA = ROOT / "generative_agents"
ASSET_ROOT = ROOT / "docs" / "book" / "assets"
STATIC_ROOT = GA / "frontend" / "static" / "assets" / "village"
CH14_SCRIPT = Path(__file__).with_name("ch14_world_model_demo.py")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GA))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CH14 = load_module(CH14_SCRIPT, "ch14_world_model_demo_for_ch17_23")
Maze = CH14.Maze
render_tilemap_crop = CH14.render_tilemap_crop
TILEMAP_PATH = CH14.TILEMAP_PATH


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
    return ImageFont.load_default()


FONT = {
    "title": load_font(34, True),
    "subtitle": load_font(18),
    "h": load_font(21, True),
    "body": load_font(17),
    "body_bold": load_font(17, True),
    "small": load_font(14),
    "mono": load_font(15),
}

RESAMPLE_NEAREST = getattr(getattr(Image, "Resampling", Image), "NEAREST")
RESAMPLE_BICUBIC = getattr(getattr(Image, "Resampling", Image), "BICUBIC")

COLORS = {
    "paper": "#f6f1e8",
    "ink": "#232323",
    "muted": "#6f6a61",
    "line": "#d8cdbf",
    "purple": "#7c3aed",
    "blue": "#2563eb",
    "teal": "#0f766e",
    "orange": "#b45309",
    "red": "#be123c",
    "green": "#166534",
}


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if not current or text_size(draw, candidate, font)[0] <= width:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    width: int,
    line_gap: int = 5,
    max_lines: int | None = None,
) -> int:
    lines = wrap(draw, text, font, width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("，。；,.; ") + "..."
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        y += text_size(draw, line or " ", font)[1] + line_gap
    return y


def base_canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (1600, 900), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((46, 34), title, fill=COLORS["ink"], font=FONT["title"])
    draw.text((50, 84), subtitle, fill=COLORS["muted"], font=FONT["subtitle"])
    return image, draw


def save(image: Image.Image, chapter: int, file_name: str) -> Path:
    out = ASSET_ROOT / f"chapter_{chapter}" / file_name
    out.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out, quality=95)
    return out


def draw_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    body: list[str],
    accent: str,
    fill: str = "#fffdf8",
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=8, fill=fill, outline="#d7c9b9", width=2)
    draw.rounded_rectangle([x1, y1, x2, y1 + 42], radius=8, fill=accent)
    draw.rectangle([x1, y1 + 24, x2, y1 + 42], fill=accent)
    draw.text((x1 + 14, y1 + 10), title, fill="#ffffff", font=FONT["body_bold"])
    y = y1 + 58
    for item in body:
        y = draw_wrapped(draw, x1 + 14, y, item, FONT["body"], COLORS["ink"], x2 - x1 - 28, max_lines=4)
        y += 8


def draw_window(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    lines: list[str],
    accent: str,
    fill: str = "#111827",
    max_lines: int | None = None,
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=8, fill=fill, outline="#2f3542", width=2)
    draw.rounded_rectangle([x1, y1, x2, y1 + 36], radius=8, fill="#1f2937")
    draw.rectangle([x1, y1 + 22, x2, y1 + 36], fill="#1f2937")
    draw.ellipse([x1 + 14, y1 + 12, x1 + 24, y1 + 22], fill="#ef4444")
    draw.ellipse([x1 + 30, y1 + 12, x1 + 40, y1 + 22], fill="#f59e0b")
    draw.ellipse([x1 + 46, y1 + 12, x1 + 56, y1 + 22], fill="#22c55e")
    draw.text((x1 + 70, y1 + 9), title, fill="#f8fafc", font=FONT["small"])
    y = y1 + 50
    use_lines = lines if max_lines is None else lines[:max_lines]
    for line in use_lines:
        y = draw_wrapped(draw, x1 + 16, y, line, FONT["mono"], accent, x2 - x1 - 32, line_gap=4, max_lines=2)
        if y > y2 - 24:
            break


def paste_portrait(image: Image.Image, name: str, box: tuple[int, int, int, int], label: str | None = None) -> None:
    path = STATIC_ROOT / "agents" / name / "portrait.png"
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=8, fill="#ffffff", outline="#d7c9b9", width=2)
    if path.exists():
        portrait = Image.open(path).convert("RGBA")
        portrait.thumbnail((x2 - x1 - 18, y2 - y1 - 42), RESAMPLE_NEAREST)
        px = x1 + (x2 - x1 - portrait.width) // 2
        py = y1 + 10
        image.alpha_composite(portrait, (px, py))
    draw.text((x1 + 10, y2 - 28), label or name, fill=COLORS["ink"], font=FONT["body_bold"])


def build_maze() -> Maze:
    return Maze(copy.deepcopy(load_json(STATIC_ROOT / "maze.json")), None)


def render_map_panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    maze: Maze,
    focus_coords: set[tuple[int, int]],
    overlays: list[dict],
    title: str,
    subtitle: str,
    label_points: list[tuple[tuple[int, int], str, str]] | None = None,
) -> None:
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=8, fill="#efe8dc", outline="#d7c9b9", width=2)
    draw.text((x1 + 18, y1 + 14), title, fill=COLORS["ink"], font=FONT["h"])
    draw.text((x1 + 18, y1 + 42), subtitle, fill=COLORS["muted"], font=FONT["small"])

    all_coords = set(focus_coords)
    for overlay in overlays:
        all_coords.update(overlay["coords"])
    if not all_coords:
        all_coords = {(0, 0)}
    margin = 3
    min_x = max(min(x for x, _ in all_coords) - margin, 0)
    max_x = min(max(x for x, _ in all_coords) + margin, maze.maze_width - 1)
    min_y = max(min(y for _, y in all_coords) - margin, 0)
    max_y = min(max(y for _, y in all_coords) + margin, maze.maze_height - 1)

    tilemap_config = load_json(TILEMAP_PATH)
    tile_w, tile_h = tilemap_config["tilewidth"], tilemap_config["tileheight"]
    crop = render_tilemap_crop(tilemap_config, min_x, min_y, max_x, max_y).convert("RGBA")
    overlay_img = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay_img)

    def rect(coord: tuple[int, int], inset: int = 0) -> list[int]:
        x, y = coord
        px = (x - min_x) * tile_w + inset
        py = (y - min_y) * tile_h + inset
        return [px, py, px + tile_w - 1 - inset * 2, py + tile_h - 1 - inset * 2]

    for overlay in overlays:
        for coord in overlay["coords"]:
            if not (min_x <= coord[0] <= max_x and min_y <= coord[1] <= max_y):
                continue
            style = overlay.get("style", "rect")
            if style == "line":
                continue
            if style == "ellipse":
                overlay_draw.ellipse(rect(coord, overlay.get("inset", 4)), fill=overlay["fill"], outline=overlay.get("outline"), width=overlay.get("width", 2))
            else:
                overlay_draw.rectangle(rect(coord, overlay.get("inset", 0)), fill=overlay["fill"], outline=overlay.get("outline"), width=overlay.get("width", 1))

    for overlay in overlays:
        if overlay.get("style") != "line":
            continue
        coords = [coord for coord in overlay["coords"] if min_x <= coord[0] <= max_x and min_y <= coord[1] <= max_y]
        if len(coords) < 2:
            continue
        points = [
            ((x - min_x) * tile_w + tile_w // 2, (y - min_y) * tile_h + tile_h // 2)
            for x, y in coords
        ]
        overlay_draw.line(points, fill=overlay["fill"], width=overlay.get("width", 4), joint="curve")

    composed = Image.alpha_composite(crop, overlay_img)
    target_w = x2 - x1 - 36
    target_h = y2 - y1 - 96
    scale = min(target_w / composed.width, target_h / composed.height)
    resized = composed.resize((max(1, int(composed.width * scale)), max(1, int(composed.height * scale))), RESAMPLE_NEAREST)
    px = x1 + 18 + (target_w - resized.width) // 2
    py = y1 + 70 + (target_h - resized.height) // 2
    image.alpha_composite(resized, (px, py))

    if label_points:
        label_draw = ImageDraw.Draw(image)
        for coord, label, color in label_points:
            lx = px + int(((coord[0] - min_x) * tile_w + tile_w // 2) * scale)
            ly = py + int(((coord[1] - min_y) * tile_h + tile_h // 2) * scale)
            label_draw.rounded_rectangle([lx + 8, ly - 18, lx + 8 + max(66, len(label) * 15), ly + 8], radius=6, fill="#fffdf8", outline=color, width=2)
            label_draw.text((lx + 14, ly - 15), label, fill=color, font=FONT["small"])


def first_active_frame(sim_name: str) -> tuple[str, dict]:
    movement = load_json(GA / "results" / "compressed" / sim_name / "movement.json")
    frames = movement["all_movement"]
    for key in sorted((k for k in frames.keys() if k.isdigit()), key=int):
        frame = frames[key]
        if frame and any("action" in data or "description" in data for data in frame.values()):
            return key, frame
    raise RuntimeError(f"No active frame in {sim_name}")


def collect_path_points(sim_name: str, agent_name: str, limit: int = 120) -> list[tuple[int, int]]:
    movement = load_json(GA / "results" / "compressed" / sim_name / "movement.json")
    points: list[tuple[int, int]] = []
    frames = movement["all_movement"]
    for key in sorted((k for k in frames.keys() if k.isdigit()), key=int):
        if len(points) >= limit:
            break
        data = frames[key].get(agent_name)
        if data and data.get("movement"):
            coord = tuple(data["movement"])
            if not points or points[-1] != coord:
                points.append(coord)
    return points


def chapter17() -> Path:
    image, draw = base_canvas(
        "图 17-2：站在小镇地图里看感知函数 Agent.percept()",
        "真实地图、真实回放坐标、真实感知配置共同决定一个角色此刻能看见什么。",
    )
    maze = build_maze()
    config = load_json(GA / "data" / "config.json")["agent"]["percept"]
    frame_key, frame = first_active_frame("book-config-ai-seminar")
    agent_coords = {name: tuple(data["movement"]) for name, data in frame.items() if data.get("movement")}
    coord = next(iter(agent_coords.values()))
    scope = maze.get_scope(coord, config)
    arena = maze.tile_at(coord).get_address("arena")
    same_arena = [tile for tile in scope if tile.get_address("arena") == arena]
    event_tiles = [tile.coord for tile in same_arena if tile.get_events()]

    render_map_panel(
        image,
        (46, 124, 1050, 800),
        maze,
        set(agent_coords.values()) | {tile.coord for tile in scope},
        [
            {"coords": {tile.coord for tile in scope}, "fill": (255, 230, 80, 54), "outline": None},
            {"coords": {tile.coord for tile in same_arena}, "fill": (58, 176, 255, 88), "outline": None},
            {"coords": set(event_tiles), "fill": (239, 68, 68, 210), "outline": "#ffffff", "style": "ellipse", "inset": 5, "width": 2},
            {"coords": set(agent_coords.values()), "fill": (124, 58, 237, 235), "outline": "#ffffff", "style": "ellipse", "inset": 3, "width": 3},
        ],
        "真实回放帧：book-config-ai-seminar",
        f"frame={frame_key}, 坐标 coord={coord}, 当前场所 arena={' / '.join(arena)}",
        [(coord, "阿伊莎/克劳斯", COLORS["purple"])],
    )
    draw_card(
        draw,
        (1084, 132, 1548, 330),
        "这不是全局视野",
        [
            f"视野范围 vision：{len(scope)} 个地图格子 tile",
            f"同场所 arena：{len(same_arena)} 个候选格子",
            f"地图格子事件 tile events：{len(event_tiles)} 处",
        ],
        COLORS["purple"],
    )
    draw_card(
        draw,
        (1084, 354, 1548, 552),
        "读图顺序",
        [
            "黄色是视野范围，蓝色是同一场所，红点是可读事件。",
            "角色不读取整张小镇地图，只把局部世界变成本步观察 concepts。",
        ],
        COLORS["teal"],
    )
    draw_window(
        draw,
        (1084, 578, 1548, 800),
        "控制台 stdout / 证据 trace",
        [
            "chapter17 percept:",
            f"坐标 coord={coord}",
            f"scope_count={len(scope)}",
            f"same_arena_tiles={len(same_arena)}",
            f"events_in_same_arena={len(event_tiles)}",
        ],
        "#a7f3d0",
        max_lines=8,
    )
    return save(image, 17, "ch17_perception_funnel.png")


def chapter18() -> Path:
    image, draw = base_canvas(
        "图 18-2：一条事件如何变成可检索记忆",
        "把世界事实 Event、记忆节点 Concept、元数据 metadata、向量索引和检索权重放在同一张证据桌面上。",
    )
    trace = load_json(ASSET_ROOT / "chapter_18" / "ch18_memory_trace.json") if (ASSET_ROOT / "chapter_18" / "ch18_memory_trace.json").exists() else {}
    associate_config = trace.get("associate_config") or load_json(GA / "data" / "config.json")["agent"]["associate"]
    metadata_keys = trace.get("add_node_metadata_keys", [])

    paste_portrait(image, "阿伊莎", (58, 136, 196, 302), "阿伊莎")
    draw_card(
        draw,
        (226, 136, 560, 302),
        "世界事实 Event",
        [
            "主语 subject / 谓词 predicate / 宾语 object",
            "address 与 describe 组成事件文本",
            "例：图书馆桌子 此时 空闲",
        ],
        COLORS["purple"],
    )
    draw_card(
        draw,
        (590, 136, 1018, 302),
        "记忆节点 Concept",
        [
            "事件被包装成 node_id + node_type",
            "重要性 poignancy、create、expire、access 进入元数据 metadata",
        ],
        COLORS["blue"],
    )
    draw_window(
        draw,
        (58, 334, 742, 730),
        "associate.py / add_node 元数据 metadata",
        [
            "{",
            *[f'  "{key}": ...,' for key in metadata_keys],
            "}",
            "",
            "evidence boundary:",
            "填充证据 filling/evidence 没有写入元数据 metadata",
        ],
        "#bfdbfe",
        max_lines=18,
    )
    draw_card(
        draw,
        (780, 334, 1168, 522),
        "当前向量嵌入 embedding",
        [
            f"provider={associate_config['embedding']['provider']}",
            f"model={associate_config['embedding']['model']}",
            f"retention={associate_config['retention']}",
        ],
        COLORS["teal"],
    )
    draw_card(
        draw,
        (1198, 334, 1542, 522),
        "检索权重",
        [
            "近因 recency",
            "相关性 relevance",
            "重要性 importance",
            "final_score = 三者相加",
        ],
        COLORS["orange"],
    )
    draw_window(
        draw,
        (780, 548, 1542, 730),
        "证据 trace JSON：ch18_memory_trace.json",
        [
            f"记忆类型 memory_types={','.join(trace.get('memory_types', ['event', 'thought', 'chat']))}",
            "公开方法 public_methods=" + ", ".join(trace.get("public_methods", [])[:4]) + "...",
            trace.get("retrieval_formula", "final_score = recency + relevance + importance"),
        ],
        "#fde68a",
        max_lines=10,
    )
    draw_wrapped(
        draw,
        58,
        780,
        "读法：这张图不是把记忆画成一个框，而是把“事件文本、元数据 metadata 字段、向量嵌入 embedding 配置、检索公式”摆在一起。记忆系统真正可检查的证据就在这些文件和字段里。",
        FONT["h"],
        COLORS["ink"],
        1450,
        max_lines=2,
    )
    return save(image, 18, "ch18_memory_retrieval_chain.png")


def chapter19() -> Path:
    image, draw = base_canvas(
        "图 19-2：一天日程如何落到可执行行动",
        "真实断点 checkpoint 里的日程 Schedule，不是一段说明文字，而是一张 24 小时时间表。",
    )
    checkpoint = load_json(GA / "results" / "checkpoints" / "book-party-pair" / "simulate-20240214-0800.json")
    agent = checkpoint["agents"]["伊莎贝拉"]
    schedule = agent["schedule"]["daily_schedule"]
    decompose_plan = next((plan for plan in schedule if plan.get("decompose")), schedule[0])

    paste_portrait(image, "伊莎贝拉", (62, 130, 210, 304), "伊莎贝拉")
    draw_card(
        draw,
        (238, 130, 610, 304),
        "角色目标 currently",
        [
            agent["currently"][:92] + "...",
            "目标会进入日程提示词 prompt，影响一天计划。",
        ],
        COLORS["purple"],
    )
    timeline_box = (62, 342, 1538, 574)
    draw.rounded_rectangle(timeline_box, radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((84, 362), "真实日程 daily_schedule 时间轴", fill=COLORS["ink"], font=FONT["h"])
    x0, y0, x1, y1 = 96, 430, 1508, 512
    draw.line([x0, y1 + 16, x1, y1 + 16], fill="#9a8f82", width=2)
    for hour in range(0, 25, 2):
        x = x0 + int((x1 - x0) * hour / 24)
        draw.line([x, y1 + 8, x, y1 + 24], fill="#9a8f82", width=2)
        draw.text((x - 12, y1 + 30), f"{hour:02d}", fill=COLORS["muted"], font=FONT["small"])
    palette = ["#93c5fd", "#bfdbfe", "#fef3c7", "#fed7aa", "#bbf7d0", "#fecdd3"]
    for idx, plan in enumerate(schedule):
        start = plan["start"]
        end = plan["start"] + plan["duration"]
        bx0 = x0 + int((x1 - x0) * start / 1440)
        bx1 = x0 + int((x1 - x0) * end / 1440)
        color = palette[idx % len(palette)]
        draw.rounded_rectangle([bx0, y0, max(bx1, bx0 + 8), y1], radius=5, fill=color, outline="#ffffff", width=1)
        if bx1 - bx0 > 90:
            draw_wrapped(draw, bx0 + 6, y0 + 8, plan["describe"], FONT["small"], "#1f2937", bx1 - bx0 - 12, max_lines=2)

    draw_card(
        draw,
        (62, 612, 618, 792),
        "被拆解的粗计划",
        [
            f"{decompose_plan['start']//60:02d}:{decompose_plan['start']%60:02d} 开始",
            decompose_plan["describe"],
            f"持续 {decompose_plan['duration']} 分钟",
        ],
        COLORS["teal"],
    )
    decompose = decompose_plan.get("decompose", [])[:7]
    draw.rounded_rectangle([650, 612, 1538, 792], radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((672, 630), "子计划 decompose：行动已经能落到分钟级", fill=COLORS["ink"], font=FONT["h"])
    for i, item in enumerate(decompose):
        x = 676 + (i % 4) * 212
        y = 676 + (i // 4) * 52
        draw.rounded_rectangle([x, y, x + 190, y + 38], radius=6, fill="#eef2ff", outline="#c7d2fe")
        draw_wrapped(draw, x + 8, y + 8, f"{item['duration']}m {item['describe']}", FONT["small"], "#1e1b4b", 174, max_lines=1)
    draw_wrapped(
        draw,
        62,
        824,
        "读法：日程 Schedule 的真实形态是时间轴。提示词 prompt 生成的不是“生活感”这个抽象概念，而是能被当前计划函数 current_plan() 读取、能被行动落地函数 _determine_action() 落地的分钟级计划。",
        FONT["h"],
        COLORS["ink"],
        1460,
        max_lines=2,
    )
    return save(image, 19, "ch19_schedule_pipeline.png")


def chapter20() -> Path:
    image, draw = base_canvas(
        "图 20-2：一次真实对话发生在小镇空间里",
        "社交不是聊天气泡生成，而是同一场所、关系记忆、对话文本和日程写回共同成立。",
    )
    maze = build_maze()
    frame_key, frame = first_active_frame("book-config-ai-seminar")
    coords = {name: tuple(data["movement"]) for name, data in frame.items() if data.get("movement")}
    coord = next(iter(coords.values()))
    scope = maze.get_scope(coord, load_json(GA / "data" / "config.json")["agent"]["percept"])
    arena = maze.tile_at(coord).get_address("arena")
    same_arena = [tile for tile in scope if tile.get_address("arena") == arena]
    render_map_panel(
        image,
        (46, 124, 858, 792),
        maze,
        set(coords.values()) | {tile.coord for tile in same_arena},
        [
            {"coords": {tile.coord for tile in same_arena}, "fill": (58, 176, 255, 82), "outline": None},
            {"coords": set(coords.values()), "fill": (190, 24, 93, 230), "outline": "#ffffff", "style": "ellipse", "inset": 3, "width": 3},
        ],
        "对话发生地：奥克山学院 / 图书馆 / 图书馆桌子",
        f"frame={frame_key}, 同一场所 arena 才有社交触发基础",
        [(coord, "图书馆桌子", COLORS["red"])],
    )
    conversation = load_json(GA / "results" / "checkpoints" / "book-config-ai-seminar" / "conversation.json")
    conv_time = sorted(conversation.keys())[0]
    conv_obj = conversation[conv_time][0]
    conv_title, turns = next(iter(conv_obj.items()))
    paste_portrait(image, "克劳斯", (902, 132, 1024, 280), "克劳斯")
    paste_portrait(image, "阿伊莎", (1042, 132, 1164, 280), "阿伊莎")
    draw_card(
        draw,
        (1188, 132, 1548, 280),
        "真实对话文件 conversation.json",
        [conv_time, conv_title.split(" @ ")[-1], f"轮数 turns={len(turns)}"],
        COLORS["purple"],
    )
    draw.rounded_rectangle([902, 310, 1548, 792], radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((924, 330), "真实对话片段", fill=COLORS["ink"], font=FONT["h"])
    y = 374
    for speaker, text in turns[:4]:
        color = COLORS["blue"] if speaker == "克劳斯" else COLORS["teal"]
        draw.rounded_rectangle([924, y, 1526, y + 78], radius=10, fill="#f8fafc", outline=color, width=2)
        draw.text((942, y + 10), speaker, fill=color, font=FONT["body_bold"])
        draw_wrapped(draw, 1010, y + 10, text, FONT["small"], COLORS["ink"], 492, max_lines=3)
        y += 94
    draw_wrapped(
        draw,
        902,
        824,
        "读法：地图说明两人同场相遇；右侧对话 JSON 说明发生了可复盘的信息交换。这比流程图更接近项目真实体验。",
        FONT["h"],
        COLORS["ink"],
        620,
        max_lines=2,
    )
    return save(image, 20, "ch20_social_loop.png")


def chapter21() -> Path:
    image, draw = base_canvas(
        "图 21-2：反思把经历钉到证据板上",
        "反思不是一句总结，而是从近期记忆、焦点问题、证据编号到 thought 写回的证据链。",
    )
    trace = load_json(ASSET_ROOT / "chapter_21" / "ch21_reflection_trace.json") if (ASSET_ROOT / "chapter_21" / "ch21_reflection_trace.json").exists() else {}
    conversation = load_json(GA / "results" / "checkpoints" / "book-config-ai-seminar" / "conversation.json")
    turns = next(iter(conversation.values()))[0]
    _, chat_turns = next(iter(turns.items()))

    draw.rounded_rectangle([58, 128, 498, 792], radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((84, 152), "近期记忆素材", fill=COLORS["ink"], font=FONT["h"])
    y = 198
    for idx, (speaker, text) in enumerate(chat_turns[:4], 1):
        draw.rounded_rectangle([84, y, 470, y + 112], radius=8, fill="#fefce8", outline="#eab308", width=2)
        draw.text((104, y + 12), f"证据候选 {idx} / {speaker}", fill=COLORS["orange"], font=FONT["body_bold"])
        draw_wrapped(draw, 104, y + 42, text, FONT["small"], COLORS["ink"], 340, max_lines=3)
        y += 132

    draw.rounded_rectangle([538, 128, 1038, 792], radius=8, fill="#f8fafc", outline="#cbd5e1", width=2)
    draw.text((564, 152), "反思触发仪表盘", fill=COLORS["ink"], font=FONT["h"])
    threshold = trace.get("poignancy_max", 150)
    gauge_x, gauge_y = 598, 248
    draw.arc([gauge_x, gauge_y, gauge_x + 340, gauge_y + 340], start=180, end=360, fill="#cbd5e1", width=28)
    draw.arc([gauge_x, gauge_y, gauge_x + 340, gauge_y + 340], start=180, end=330, fill="#be123c", width=28)
    draw.text((gauge_x + 102, gauge_y + 114), f"{threshold}", fill=COLORS["red"], font=FONT["title"])
    draw.text((gauge_x + 42, gauge_y + 160), "重要性阈值 poignancy_max", fill=COLORS["muted"], font=FONT["h"])
    draw_card(
        draw,
        (580, 520, 996, 704),
        "源码边界",
        [
            f"证据写入 metadata：{trace.get('evidence_persisted_in_metadata', False)}",
            "evidence 进入 _add_concept(filling=...)",
            "但 Associate.add_node() 未持久化 filling",
        ],
        COLORS["red"],
        fill="#fff7ed",
    )

    draw.rounded_rectangle([1076, 128, 1548, 792], radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((1102, 152), "从焦点到想法 thought", fill=COLORS["ink"], font=FONT["h"])
    prompts = trace.get("prompt_template_bytes", {})
    cards = [
        ("reflect_focus", "先问：最近经历值得围绕什么问题反思？"),
        ("retrieve_focus", "再围绕 focus 检索证据节点。"),
        ("reflect_insights", "生成 thought + evidence 编号。"),
        ("Associate", "把 thought 写回记忆流，影响后续对话和计划。"),
    ]
    y = 204
    for name, body in cards:
        draw.rounded_rectangle([1102, y, 1522, y + 96], radius=8, fill="#eef2ff", outline="#818cf8", width=2)
        size = prompts.get(name, "")
        suffix = f" / {size} bytes" if size else ""
        draw.text((1122, y + 12), name + suffix, fill="#3730a3", font=FONT["body_bold"])
        draw_wrapped(draw, 1122, y + 42, body, FONT["small"], COLORS["ink"], 360, max_lines=2)
        y += 124
    draw_wrapped(
        draw,
        58,
        824,
        "读法：反思图要让读者看见“证据被选中，又被压缩成想法 thought”。如果证据 evidence 不持久化，这个断点也必须出现在图上。",
        FONT["h"],
        COLORS["ink"],
        1450,
        max_lines=2,
    )
    return save(image, 21, "ch21_reflection_pipeline.png")


def chapter22() -> Path:
    image, draw = base_canvas(
        "图 22-2：模型适配层的真实输入与输出",
        "不同模型提供方 provider 最后都要变成业务代码能继续执行的结构化结果。",
    )
    trace = load_json(ASSET_ROOT / "chapter_22" / "ch22_model_adapter_trace.json") if (ASSET_ROOT / "chapter_22" / "ch22_model_adapter_trace.json").exists() else {}
    config = load_json(GA / "data" / "config.json")["agent"]

    draw_card(
        draw,
        (58, 130, 442, 332),
        "当前推理模型提供方 provider",
        [
            f"provider={config['think']['llm']['provider']}",
            f"model={config['think']['llm']['model']}",
            "负责日程、对话、反思等行为生成",
        ],
        COLORS["purple"],
    )
    draw_card(
        draw,
        (472, 130, 856, 332),
        "当前向量嵌入 embedding",
        [
            f"provider={config['associate']['embedding']['provider']}",
            f"model={config['associate']['embedding']['model']}",
            "负责记忆检索中的语义相似度",
        ],
        COLORS["teal"],
    )
    draw_window(
        draw,
        (58, 374, 760, 742),
        "原始模型输出 raw provider output",
        [
            "<think>draft reasoning...</think>",
            '{"res": 7}',
            "",
            'prefix {"res": false} suffix',
            "",
            "bad / falsey boundary:",
            "response=0 -> response or failsafe -> 8",
        ],
        "#fef08a",
        max_lines=14,
    )
    cases = trace.get("structured_output_cases", {})
    draw_window(
        draw,
        (802, 374, 1542, 742),
        "结构化解析 parse_structured_output() / 结果契约 Result",
        [
            "Result(prompt, callback, failsafe, return_type)",
            "",
            f"clean_json_int -> {cases.get('clean_json_int', 7)}",
            f"wrapped_json_bool -> {cases.get('wrapped_json_bool', False)}",
            f"think_filtered_int -> {cases.get('think_filtered_int', 7)}",
            f"falsey_response_boundary -> {cases.get('falsey_response_boundary', 8)}",
            "",
            "输出必须变成 int / bool / dict / list，才能继续驱动 Agent。",
        ],
        "#bfdbfe",
        max_lines=16,
    )
    draw_card(
        draw,
        (890, 130, 1542, 332),
        "读图结论",
        [
            "模型适配层的重点不是换 API 地址。",
            "真正的工程边界是提示词 prompt、结构 schema、回调 callback、兜底 failsafe、统计 summary 是否稳定统一。",
        ],
        COLORS["orange"],
    )
    draw_wrapped(
        draw,
        58,
        804,
        "读法：左边是模型提供方 provider 可能吐出的原始文本，右边是项目真正需要的结构化结果。读者应该把模型层理解成“把不稳定文本压成可执行数据”的地方。",
        FONT["h"],
        COLORS["ink"],
        1450,
        max_lines=2,
    )
    return save(image, 22, "ch22_model_adapter_chain.png")


def chapter23() -> Path:
    image, draw = base_canvas(
        "图 23-2：从 checkpoint 到前端回放的一段真实轨迹",
        "回放图要看见小镇、路径、移动回放文件 movement.json 和时间线文件 simulation.md，而不是只看数据流箭头。",
    )
    maze = build_maze()
    sim_name = "book-party-pair"
    isabella = collect_path_points(sim_name, "伊莎贝拉", 140)
    aisha = collect_path_points(sim_name, "阿伊莎", 140)
    focus = set(isabella + aisha)
    render_map_panel(
        image,
        (46, 124, 950, 800),
        maze,
        focus,
        [
            {"coords": isabella, "fill": (190, 24, 93, 190), "style": "line", "width": 6},
            {"coords": aisha, "fill": (37, 99, 235, 190), "style": "line", "width": 6},
            {"coords": {isabella[0], isabella[-1]}, "fill": (190, 24, 93, 230), "outline": "#ffffff", "style": "ellipse", "inset": 3, "width": 3},
            {"coords": {aisha[0], aisha[-1]}, "fill": (37, 99, 235, 230), "outline": "#ffffff", "style": "ellipse", "inset": 3, "width": 3},
        ],
        "真实移动回放文件 movement.json 轨迹",
        "红线=伊莎贝拉，蓝线=阿伊莎；每个仿真步被展开成前端回放帧",
        [(isabella[0], "伊莎贝拉 start", COLORS["red"]), (aisha[0], "阿伊莎 start", COLORS["blue"])],
    )
    movement = load_json(GA / "results" / "compressed" / sim_name / "movement.json")
    simulation = read_text(GA / "results" / "compressed" / sim_name / "simulation.md")
    checkpoints = sorted((GA / "results" / "checkpoints" / sim_name).glob("simulate-*.json"))
    frame_keys = [k for k in movement["all_movement"] if k.isdigit()]
    non_empty = [k for k in frame_keys if movement["all_movement"][k]]
    draw_card(
        draw,
        (988, 132, 1542, 318),
        "真实压缩统计",
        [
            f"断点 checkpoint JSON：{len(checkpoints)} 个",
            f"移动帧 movement frames：{len(frame_keys)} 帧",
            f"非空帧 non-empty frames：{len(non_empty)} 帧",
            "agents：伊莎贝拉 / 阿伊莎",
        ],
        COLORS["purple"],
    )
    draw_window(
        draw,
        (988, 350, 1542, 560),
        "移动回放文件 movement.json 摘录",
        [
            '"start_datetime": "2024-02-14T08:00:00"',
            '"persona_init_pos": {"伊莎贝拉": [72,14], "阿伊莎": [118,61]}',
            '"all_movement": {"1": {"伊莎贝拉": {"movement": [72,14] ...}}}',
        ],
        "#d9f99d",
        max_lines=8,
    )
    sim_lines = [line for line in simulation.splitlines() if line.strip()][:10]
    draw_window(
        draw,
        (988, 590, 1542, 800),
        "时间线文件 simulation.md 摘录",
        sim_lines,
        "#fde68a",
        max_lines=8,
    )
    draw_wrapped(
        draw,
        46,
        824,
        "读法：断点 checkpoint 保留完整状态，移动回放文件 movement.json 把坐标展开成前端可播放帧，时间线文件 simulation.md 把状态变化压成人类可读时间线。三者一起才构成回放证据。",
        FONT["h"],
        COLORS["ink"],
        1450,
        max_lines=2,
    )
    return save(image, 23, "ch23_replay_dataflow.png")


def main() -> int:
    paths = [
        chapter17(),
        chapter18(),
        chapter19(),
        chapter20(),
        chapter21(),
        chapter22(),
        chapter23(),
    ]
    for path in paths:
        print(f"generated: {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
