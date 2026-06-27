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

from PIL import Image, ImageDraw, ImageFilter, ImageFont


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


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str, width: int = 5) -> None:
    draw.line([start, end], fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 15
    left = (end[0] - size * math.cos(angle - math.pi / 6), end[1] - size * math.sin(angle - math.pi / 6))
    right = (end[0] - size * math.cos(angle + math.pi / 6), end[1] - size * math.sin(angle + math.pi / 6))
    draw.polygon([end, left, right], fill=color)


def draw_tag(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: str, ink: str = "#ffffff") -> tuple[int, int]:
    x, y = xy
    w, h = text_size(draw, text, FONT["small"])
    draw.rounded_rectangle([x, y, x + w + 22, y + h + 14], radius=12, fill=fill)
    draw.text((x + 11, y + 7), text, fill=ink, font=FONT["small"])
    return x + w + 30, y


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
    image = Image.new("RGBA", (2000, 1200), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((50, 34), "图 18-2：记忆节点在项目里到底长什么样", fill=COLORS["ink"], font=FONT["title"])
    draw.text((54, 86), "从输入 Event 到 event/chat/thought，再到 docstore、embedding 和 retrieved concepts，把真实数据结构摊开看。", fill=COLORS["muted"], font=FONT["subtitle"])

    def associate_base(sim: str, agent: str) -> Path:
        return GA / "results" / "checkpoints" / sim / "storage" / agent / "associate"

    def node_sample(base: Path, node_id: str) -> dict:
        doc = load_json(base / "docstore.json")["docstore/data"][node_id]["__data__"]
        vector_store = load_json(base / "default__vector_store.json")
        embedding = vector_store["embedding_dict"][node_id]
        return {
            "id": node_id,
            "text": doc["text"],
            "metadata": doc["metadata"],
            "embedding": embedding,
        }

    event_base = associate_base("book-smoke", "克劳斯")
    chat_base = associate_base("book-config-ai-seminar", "阿伊莎")
    event_node = node_sample(event_base, "node_2")
    chat_node = node_sample(chat_base, "node_1")
    thought_node = node_sample(chat_base, "node_0")
    event_memory = load_json(GA / "results" / "checkpoints" / "book-smoke" / "simulate-20240213-1000.json")["agents"]["克劳斯"]["associate"]["memory"]
    chat_memory = load_json(GA / "results" / "checkpoints" / "book-config-ai-seminar" / "simulate-20240213-1010.json")["agents"]["阿伊莎"]["associate"]["memory"]
    event_index = load_json(event_base / "index_store.json")["index_store/data"]
    event_nodes_dict = json.loads(next(iter(event_index.values()))["__data__"])["nodes_dict"]
    event_index_config = load_json(event_base / "index_config.json")

    def short(text: str, n: int = 42) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"

    def embedding_preview(node: dict) -> str:
        values = ", ".join(f"{v:.4f}" for v in node["embedding"][:5])
        return f"embedding[0:5]=[{values}, ...], dim={len(node['embedding'])}"

    def draw_section(box: tuple[int, int, int, int], title: str, subtitle: str, color: str) -> None:
        x1, y1, x2, y2 = box
        draw.rounded_rectangle([x1, y1, x2, y2], radius=12, fill="#fffdf8", outline="#d7c9b9", width=2)
        draw.rounded_rectangle([x1, y1, x2, y1 + 48], radius=12, fill=color)
        draw.rectangle([x1, y1 + 26, x2, y1 + 48], fill=color)
        draw.text((x1 + 18, y1 + 13), title, fill="#ffffff", font=FONT["h"])
        draw.text((x1 + 18, y1 + 60), subtitle, fill=COLORS["muted"], font=FONT["small"])

    draw_section((46, 130, 510, 1060), "输入 Input：一条事件 Event", "这是写入记忆之前的对象，还不是索引节点。", COLORS["purple"])
    paste_portrait(image, "克劳斯", (72, 218, 190, 372), "克劳斯")
    draw_window(
        draw,
        (212, 218, 486, 506),
        "Event.to_dict()",
        [
            "{",
            '  "subject": "克劳斯",',
            '  "predicate": "此时",',
            '  "object": "阅读并批注选中的学术文章",',
            '  "describe": "克劳斯 阅读并批注选中的学术文章",',
            '  "address": ["the Ville", "奥克山学院", "图书馆", "图书馆桌子"]',
            "}",
        ],
        "#bfdbfe",
        max_lines=10,
    )
    draw_card(
        draw,
        (72, 540, 486, 720),
        "输入不是记忆",
        [
            "Event 只说明发生了什么。",
            "它没有 node_id、node_type、embedding。",
            "只有进入 Associate.add_node() 后，才变成可检索记忆节点。",
        ],
        COLORS["blue"],
    )
    draw_window(
        draw,
        (72, 752, 486, 1018),
        "进入写入函数",
        [
            "Associate.add_node(",
            '  node_type="event",',
            "  event=Event(...),",
            "  poignancy=2,",
            "  create=20240213-09:40:00",
            ")",
        ],
        "#fde68a",
        max_lines=8,
    )

    draw_section((550, 130, 1220, 1060), "处理 Process：三种记忆节点", "处理后的结果不是一个 node_id，而是 TextNode + metadata + embedding。", COLORS["blue"])

    def draw_memory_node(y: int, title: str, memory_key: str, node: dict, memory: dict, color: str) -> None:
        meta = node["metadata"]
        draw.rounded_rectangle([582, y, 1188, y + 246], radius=10, fill="#ffffff", outline=color, width=3)
        draw_tag(draw, (602, y + 14), title, color)
        memory_line = f'{memory_key} = {memory[meta["node_type"]]}'
        draw_wrapped(draw, 760, y + 18, memory_line, FONT["mono"], COLORS["ink"], 400, line_gap=2, max_lines=1)
        lines = [
            f'TextNode.id_ = "{node["id"]}"',
            f'text = "{short(node["text"], 52)}"',
            "metadata = {",
            f'  node_type: "{meta["node_type"]}", subject: "{meta["subject"]}",',
            f'  predicate: "{meta["predicate"]}", object: "{short(str(meta["object"]), 28)}",',
            f'  poignancy: {meta["poignancy"]}, create: "{meta["create"]}",',
            "}",
            f'embedding_dict["{node["id"]}"]: {embedding_preview(node)}',
        ]
        yy = y + 58
        for line in lines:
            yy = draw_wrapped(draw, 602, yy, line, FONT["mono"], "#111827", 560, line_gap=3, max_lines=1)

    draw_memory_node(222, "事件 event", 'memory["event"]', event_node, event_memory, "#2563eb")
    draw_memory_node(500, "聊天 chat", 'memory["chat"]', chat_node, chat_memory, "#0f766e")
    draw_memory_node(778, "想法 thought", 'memory["thought"]', thought_node, chat_memory, "#b45309")

    draw_section((1260, 130, 1954, 1060), "输出 Output：索引结构与检索结果", "输出不是“prompt 上下文”四个字，而是可打印的节点、向量和文本列表。", COLORS["teal"])
    draw_window(
        draw,
        (1288, 218, 1928, 520),
        "docstore.json：完整 TextNode 样例",
        [
            '"docstore/data": {',
            f'  "{event_node["id"]}": {{',
            '    "__data__": {',
            '      "text": "克劳斯 阅读并批注选中的学术文章",',
            '      "metadata": {',
            '        node_type:event, subject:克劳斯, predicate:此时,',
            '        object:阅读并批注选中的学术文章,',
            '        address:the Ville:奥克山学院:图书馆:图书馆桌子,',
            '        poignancy:2, create:20240213-09:40:00,',
            '        expire:20240314-09:40:00, access:20240213-09:40:00',
            '      }',
            "    }",
            "  }",
            "}",
        ],
        "#bfdbfe",
        max_lines=12,
    )
    draw_window(
        draw,
        (1288, 548, 1608, 704),
        "vector_store.json",
        [
            '"embedding_dict": {',
            f'  "{event_node["id"]}": [',
            "    -0.0124, -0.0081, 0.0131,",
            "    0.0004, -0.0153, ...",
            "  ]",
            "}",
            "这是 1536 维 float 向量，不是文本。",
            '"metadata_dict": {...}',
        ],
        "#c7f9cc",
        max_lines=10,
    )
    draw_window(
        draw,
        (1630, 548, 1928, 704),
        "index_store.json",
        [
            'nodes_dict={',
            '  "node_0":"node_0", "node_1":"node_1", "node_2":"node_2", ...}',
            f'max_nodes={event_index_config["max_nodes"]}',
            "node_id 是门牌号。",
            "完整内容在 docstore / vector_store。",
        ],
        "#fde68a",
        max_lines=6,
    )
    draw_card(
        draw,
        (1288, 732, 1928, 1042),
        "真实检索输出 retrieved concepts",
        [
            'retrieve_events() ->',
            f'  event: "{short(event_node["text"], 54)}"',
            'retrieve_chats("克劳斯") ->',
            f'  chat: "{short(chat_node["text"], 54)}"',
            'retrieve_focus(["今天计划", "奖学金评分"]) ->',
            f'  thought: "{short(thought_node["text"], 54)}"',
            "这些 Concept.describe 文本才会进入 prompt 上下文。",
        ],
        COLORS["orange"],
    )

    draw_arrow(draw, (510, 560), (550, 560), COLORS["blue"])
    draw_arrow(draw, (1220, 560), (1260, 560), COLORS["blue"])
    draw_wrapped(
        draw,
        60,
        1096,
        "读法：Event 输入经过 Associate.add_node() 后，分别落成 event/chat/thought 三类 TextNode。TextNode 的 text 进入语义向量，metadata 保留结构字段，node_id 写进 memory 列表；检索时返回的是 Concept.describe 文本列表，再被拼进 prompt。",
        FONT["body_bold"],
        COLORS["ink"],
        1880,
        max_lines=2,
    )
    return save(image, 18, "ch18_memory_retrieval_chain.png")


def chapter19() -> Path:
    image = Image.new("RGBA", (2000, 1200), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((50, 34), "图 19-2：日程不是表格，它把克劳斯送到图书馆桌子前", fill=COLORS["ink"], font=FONT["title"])
    draw.text(
        (54, 88),
        "真实 book-smoke 断点：currently、daily_schedule、decompose、Action 和地图坐标共同解释 09:50 为什么正在写论文。",
        fill=COLORS["muted"],
        font=FONT["subtitle"],
    )
    checkpoint = load_json(GA / "results" / "checkpoints" / "book-smoke" / "simulate-20240213-1000.json")
    agent = checkpoint["agents"]["克劳斯"]
    schedule = agent["schedule"]["daily_schedule"]
    rough_plan = schedule[9]
    action = agent["action"]
    coord = tuple(agent["coord"])
    maze = build_maze()

    render_map_panel(
        image,
        (50, 128, 900, 768),
        maze,
        {coord},
        [
            {"coords": {coord}, "fill": (190, 24, 93, 235), "outline": "#ffffff", "style": "ellipse", "inset": 2, "width": 4},
        ],
        "真实地图现场：奥克山学院 / 图书馆 / 图书馆桌子",
        f"克劳斯 coord={coord}，Action.start={action['start']}，duration={action['duration']} 分钟",
        [(coord, "克劳斯正在写论文", COLORS["red"])],
    )
    paste_portrait(image, "克劳斯", (940, 128, 1088, 302), "克劳斯")
    draw_card(
        draw,
        (1118, 128, 1940, 302),
        "角色当前目标 currently",
        [
            agent["currently"],
            "日程 Schedule 不是随机动作表，而是把这个目标铺成一天时间结构。",
        ],
        COLORS["purple"],
    )

    timeline_box = (940, 342, 1940, 575)
    draw.rounded_rectangle(timeline_box, radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((968, 363), "真实日程 daily_schedule：09:00-10:00 被拆成 7 个分钟级动作", fill=COLORS["ink"], font=FONT["h"])
    x0, y0, x1, y1 = 982, 438, 1905, 512
    draw.line([x0, y1 + 16, x1, y1 + 16], fill="#9a8f82", width=2)
    for minute, label in [(540, "09:00"), (570, "09:30"), (590, "09:50"), (600, "10:00")]:
        x = x0 + int((x1 - x0) * (minute - 540) / 60)
        draw.line([x, y1 + 8, x, y1 + 24], fill="#9a8f82", width=2)
        draw.text((x - 22, y1 + 32), label, fill=COLORS["muted"], font=FONT["small"])
    palette = ["#dbeafe", "#bfdbfe", "#fef3c7", "#fed7aa", "#bbf7d0", "#fecdd3", "#c7d2fe"]
    for idx, item in enumerate(rough_plan["decompose"]):
        start = item["start"]
        end = item["start"] + item["duration"]
        bx0 = x0 + int((x1 - x0) * (start - 540) / 60)
        bx1 = x0 + int((x1 - x0) * (end - 540) / 60)
        current = start == 590
        color = "#fb7185" if current else palette[idx % len(palette)]
        outline = "#881337" if current else "#ffffff"
        draw.rounded_rectangle([bx0, y0, max(bx1, bx0 + 12), y1], radius=8, fill=color, outline=outline, width=3 if current else 1)
        if bx1 - bx0 > 72:
            draw_wrapped(draw, bx0 + 8, y0 + 9, f"{item['duration']}m {item['describe']}", FONT["small"], "#111827", bx1 - bx0 - 14, max_lines=2)

    draw_card(
        draw,
        (940, 620, 1390, 884),
        "粗计划 plan",
        [
            "09:00-10:00",
            rough_plan["describe"],
            "字段：idx=9 / start=540 / duration=60",
        ],
        COLORS["teal"],
    )

    draw_card(
        draw,
        (1425, 620, 1940, 884),
        "当前行动 Action",
        [
            action["event"]["subject"] + " " + action["event"]["object"],
            "地点 address：奥克山学院 / 图书馆 / 图书馆桌子",
            f"对象事件 obj_event：{action['obj_event']['subject']} {action['obj_event']['object']}",
        ],
        COLORS["red"],
    )

    draw_window(
        draw,
        (50, 812, 900, 1070),
        "断点 checkpoint 摘录",
        [
            '"currently": "克劳斯正在撰写一篇关于低收入社区中产阶级化影响的研究论文。"',
            '"schedule.create": "20240213-09:30:00"',
            '"plan.describe": "开始在图书馆安静角落撰写关于中产阶级化的研究论文，查阅相关文献"',
            '"de_plan.describe": "开始撰写论文中关于中产阶级化影响的段落"',
            '"action.start": "20240213-09:50:00"',
        ],
        "#d9f99d",
        max_lines=8,
    )
    draw_wrapped(
        draw,
        940,
        940,
        "读法：日程 Schedule 先给出粗计划，再由子计划 decompose 定位到 09:50 的具体动作；行动落地函数 _determine_action() 把这个动作绑定到图书馆桌子，写成角色事件和对象事件。",
        FONT["h"],
        COLORS["ink"],
        950,
        max_lines=4,
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
    image = Image.new("RGBA", (1600, 900), "#07111f")
    draw = ImageDraw.Draw(image)

    # A dark, high-contrast canvas makes reflection feel like memory being
    # developed from raw evidence instead of another boxed pipeline.
    gradient = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(900):
        ratio = y / 900
        gd.line(
            [(0, y), (1600, y)],
            fill=(
                int(9 + 19 * ratio),
                int(16 + 14 * ratio),
                int(34 + 6 * ratio),
                255,
            ),
        )
    image = Image.alpha_composite(image, gradient)
    draw = ImageDraw.Draw(image)

    trace = load_json(ASSET_ROOT / "chapter_21" / "ch21_reflection_trace.json") if (ASSET_ROOT / "chapter_21" / "ch21_reflection_trace.json").exists() else {}
    conversation = load_json(GA / "results" / "checkpoints" / "book-config-ai-seminar" / "conversation.json")
    turns = next(iter(conversation.values()))[0]
    _, chat_turns = next(iter(turns.items()))

    draw.text((46, 34), "图 21-2：反思把经历压成长期想法 thought", fill="#f8fafc", font=FONT["title"])
    draw.text(
        (50, 84),
        "真实图书馆对话先变成候选证据；重要性阈值打开后，证据被压缩成可被后续计划和对话检索的 thought。",
        fill="#b9c4d6",
        font=FONT["subtitle"],
    )

    def glow_circle(center: tuple[int, int], radius: int, color: tuple[int, int, int], alpha: int) -> None:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        cx, cy = center
        for i in range(5, 0, -1):
            r = radius + i * 24
            od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, max(8, alpha // (i + 1))))
        blurred = overlay.filter(ImageFilter.GaussianBlur(18))
        image.alpha_composite(blurred)

    def rounded_alpha_paste(src: Image.Image, xy: tuple[int, int], radius: int = 26) -> None:
        mask = Image.new("L", src.size, 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle([0, 0, src.width - 1, src.height - 1], radius=radius, fill=255)
        target = Image.new("RGBA", src.size, (0, 0, 0, 0))
        target.paste(src, (0, 0), mask)
        image.alpha_composite(target, xy)

    def paste_round_portrait(name: str, center: tuple[int, int], size: int, ring: str) -> None:
        x, y = center
        portrait_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pd = ImageDraw.Draw(portrait_layer)
        pd.ellipse([2, 2, size - 3, size - 3], fill="#0f172a", outline=ring, width=4)
        path = STATIC_ROOT / "agents" / name / "portrait.png"
        if path.exists():
            portrait = Image.open(path).convert("RGBA")
            portrait.thumbnail((size - 18, size - 18), RESAMPLE_NEAREST)
            px = (size - portrait.width) // 2
            py = (size - portrait.height) // 2 - 2
            circle = Image.new("L", portrait.size, 0)
            cd = ImageDraw.Draw(circle)
            cd.ellipse([0, 0, portrait.width - 1, portrait.height - 1], fill=255)
            portrait_layer.paste(portrait, (px, py), circle)
        image.alpha_composite(portrait_layer, (x - size // 2, y - size // 2))
        label_w, _ = text_size(draw, name, FONT["small"])
        draw.rounded_rectangle([x - label_w // 2 - 10, y + size // 2 - 10, x + label_w // 2 + 10, y + size // 2 + 18], radius=12, fill="#f8fafc", outline=ring, width=2)
        draw.text((x - label_w // 2, y + size // 2 - 6), name, fill="#0f172a", font=FONT["small"])

    def beam(start: tuple[int, int], end: tuple[int, int], color: tuple[int, int, int], width: int = 7) -> None:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.line([start, end], fill=(*color, 70), width=width + 10)
        od.line([start, end], fill=(*color, 190), width=width)
        image.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(1)))

    def speech_bubble(
        idx: int,
        speaker: str,
        text: str,
        xy: tuple[int, int],
        angle: float,
        accent: str,
        fill: str,
    ) -> tuple[int, int]:
        bubble = Image.new("RGBA", (430, 134), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bubble)
        bd.rounded_rectangle([8, 8, 408, 116], radius=24, fill=fill, outline=accent, width=3)
        bd.polygon([(52, 112), (88, 112), (64, 132)], fill=fill, outline=accent)
        bd.text((30, 22), f"证据 {idx} / {speaker}", fill=accent, font=FONT["body_bold"])
        draw_wrapped(bd, 30, 54, text, FONT["small"], "#142033", 350, max_lines=3)
        rotated = bubble.rotate(angle, resample=RESAMPLE_BICUBIC, expand=True)
        image.alpha_composite(rotated, xy)
        return xy[0] + rotated.width // 2, xy[1] + rotated.height // 2

    # Real town context: Klaus and Aisha are both at the library table [119, 24].
    tilemap_config = load_json(TILEMAP_PATH)
    tile_w = tilemap_config["tilewidth"]
    min_x, max_x, min_y, max_y = 112, 126, 17, 31
    crop = render_tilemap_crop(tilemap_config, min_x, min_y, max_x, max_y).convert("RGBA")
    crop = crop.resize((600, 600), RESAMPLE_NEAREST)
    crop = Image.alpha_composite(crop, Image.new("RGBA", crop.size, (3, 8, 18, 110)))
    shadow = Image.new("RGBA", (640, 640), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([30, 30, 630, 630], radius=28, fill=(0, 0, 0, 160))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)), (30, 150))
    rounded_alpha_paste(crop, (58, 166), radius=30)

    scale = 600 / ((max_x - min_x + 1) * tile_w)
    focus = (119, 24)
    fx = 58 + int(((focus[0] - min_x) * tile_w + tile_w // 2) * scale)
    fy = 166 + int(((focus[1] - min_y) * tile_w + tile_w // 2) * scale)
    glow_circle((fx, fy), 28, (250, 204, 21), 95)
    draw.ellipse([fx - 26, fy - 26, fx + 26, fy + 26], outline="#fde047", width=5)
    draw.text((76, 186), "真实场景：奥克山学院图书馆桌子 [119,24]", fill="#f8fafc", font=FONT["h"])
    draw.text((78, 220), "克劳斯与阿伊莎讨论校园智能体公平性", fill="#cbd5e1", font=FONT["small"])
    paste_round_portrait("克劳斯", (176, 720), 92, "#60a5fa")
    paste_round_portrait("阿伊莎", (292, 720), 92, "#2dd4bf")

    lens = (790, 462)
    thought_center = (1276, 424)
    bubble_centers = []
    picks = [chat_turns[0], chat_turns[2], chat_turns[3], chat_turns[6]]
    placements = [
        ((146, 282), -7, "#f59e0b", (255, 251, 235, 235)),
        ((286, 402), 5, "#60a5fa", (239, 246, 255, 235)),
        ((100, 554), -4, "#2dd4bf", (240, 253, 250, 235)),
        ((354, 626), 7, "#f97316", (255, 247, 237, 235)),
    ]
    for idx, ((speaker, text), (xy, angle, accent, fill)) in enumerate(zip(picks, placements), 1):
        center = speech_bubble(idx, speaker, text, xy, angle, accent, fill)
        bubble_centers.append(center)

    for center in bubble_centers:
        beam(center, lens, (251, 191, 36), width=4)
    beam(lens, thought_center, (45, 212, 191), width=10)

    # Trigger lens and threshold.
    glow_circle(lens, 130, (244, 63, 94), 82)
    draw.ellipse([lens[0] - 144, lens[1] - 144, lens[0] + 144, lens[1] + 144], fill=(15, 23, 42, 226), outline="#fb7185", width=5)
    draw.arc([lens[0] - 110, lens[1] - 110, lens[0] + 110, lens[1] + 110], start=190, end=348, fill="#e11d48", width=22)
    draw.arc([lens[0] - 110, lens[1] - 110, lens[0] + 110, lens[1] + 110], start=348, end=360, fill="#475569", width=22)
    w, _ = text_size(draw, "150", FONT["title"])
    draw.text((lens[0] - w // 2, lens[1] - 42), "150", fill="#fecdd3", font=FONT["title"])
    draw.text((lens[0] - 110, lens[1] + 4), "重要性阈值 poignancy_max", fill="#e2e8f0", font=FONT["body_bold"])
    draw.rounded_rectangle([lens[0] - 118, lens[1] + 48, lens[0] + 118, lens[1] + 82], radius=17, fill="#450a0a", outline="#f87171", width=2)
    draw.text((lens[0] - 92, lens[1] + 55), "当前案例 5/6 < 150", fill="#fecaca", font=FONT["small"])

    prompts = trace.get("prompt_template_bytes", {})
    for angle, name, color in [
        (-72, "reflect_focus", "#a78bfa"),
        (-18, "retrieve_focus", "#38bdf8"),
        (38, "reflect_insights", "#34d399"),
    ]:
        rad = math.radians(angle)
        x = thought_center[0] + int(math.cos(rad) * 226)
        y = thought_center[1] + int(math.sin(rad) * 154)
        draw.line([thought_center, (x, y)], fill=color, width=3)
        draw.ellipse([x - 42, y - 42, x + 42, y + 42], fill="#0f172a", outline=color, width=4)
        label = name.split("_")[-1]
        tw, _ = text_size(draw, label, FONT["small"])
        draw.text((x - tw // 2, y - 8), label, fill="#f8fafc", font=FONT["small"])

    glow_circle(thought_center, 150, (20, 184, 166), 88)
    hex_points = []
    for i in range(6):
        rad = math.radians(60 * i - 30)
        hex_points.append((thought_center[0] + int(math.cos(rad) * 164), thought_center[1] + int(math.sin(rad) * 138)))
    draw.polygon(hex_points, fill=(6, 32, 44, 232), outline="#67e8f9")
    draw.line(hex_points + [hex_points[0]], fill="#67e8f9", width=4)
    draw.text((thought_center[0] - 112, thought_center[1] - 82), "写回长期想法 thought", fill="#ccfbf1", font=FONT["h"])
    draw_wrapped(
        draw,
        thought_center[0] - 118,
        thought_center[1] - 42,
        "达到阈值后，近期事件 event 和想法 thought 会被聚焦、检索并压缩成新的 thought 节点。",
        FONT["body"],
        "#e0f2fe",
        238,
        max_lines=4,
    )
    draw.text((thought_center[0] - 118, thought_center[1] + 76), f"focus {prompts.get('reflect_focus', 0)} bytes", fill="#bae6fd", font=FONT["small"])
    draw.text((thought_center[0] - 118, thought_center[1] + 100), f"insights {prompts.get('reflect_insights', 0)} bytes", fill="#bae6fd", font=FONT["small"])

    torn = [(950, 666), (1508, 634), (1532, 772), (1380, 792), (972, 804)]
    draw.polygon(torn, fill="#7f1d1d", outline="#fca5a5")
    draw.text((990, 674), "源码边界：证据 evidence 没有持久化到 metadata", fill="#fee2e2", font=FONT["h"])
    boundary_lines = [
        f"evidence_persisted_in_metadata={trace.get('evidence_persisted_in_metadata', False)}",
        "evidence 进入 _add_concept(filling=...)",
        "但 Associate.add_node() 不写入 filling",
    ]
    yy = 716
    for line in boundary_lines:
        draw.text((990, yy), line, fill="#fecaca", font=FONT["body"])
        yy += 28

    draw.text(
        (48, 840),
        "读法：左侧是真实小镇对话和地图位置；中间是反思触发闸门；右侧是达到阈值后写回的 thought。红色断裂处提醒：当前源码没有把 evidence 完整持久化。",
        fill="#f8fafc",
        font=FONT["h"],
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
