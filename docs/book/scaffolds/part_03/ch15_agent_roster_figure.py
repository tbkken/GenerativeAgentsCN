#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the chapter 15 agent roster figure from real project assets."""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
START_PATH = GENERATIVE_AGENTS / "start.py"
AGENTS_DIR = GENERATIVE_AGENTS / "frontend" / "static" / "assets" / "village" / "agents"
ASSET_PATH = ROOT / "docs" / "book" / "assets" / "chapter_15" / "ch15_agent_roster.png"

CANVAS_W = 2200
MARGIN_X = 72
TOP = 56
BLOCK_W = 980
BLOCK_GAP_X = 96
BLOCK_GAP_Y = 26
HEADER_H = 84
BLOCK_PAD = 24
CARD_W = 446
CARD_H = 112
CARD_GAP = 18

GROUPS = [
    {
        "title": "奥克山学院宿舍",
        "subtitle": "学生角色从同一宿舍区开始行动",
        "names": ["阿伊莎", "克劳斯", "玛丽亚", "沃尔夫冈"],
        "accent": "#3A7D7C",
    },
    {
        "title": "林氏家族的房子",
        "subtitle": "家庭关系把教授、药店主人和学生连在一起",
        "names": ["梅", "约翰", "埃迪"],
        "accent": "#8E6B2E",
    },
    {
        "title": "莫雷诺家族的房子",
        "subtitle": "家庭空间中的日常照料与市场经营",
        "names": ["简", "汤姆"],
        "accent": "#9A4D41",
    },
    {
        "title": "塔玛拉和卡门的家",
        "subtitle": "室友关系连接供应店与儿童读物创作",
        "names": ["卡门", "塔玛拉"],
        "accent": "#6A6F2E",
    },
    {
        "title": "小镇经营者",
        "subtitle": "酒吧老板与咖啡馆老板是公共生活的高频节点",
        "names": ["亚瑟", "伊莎贝拉"],
        "accent": "#B36B35",
    },
    {
        "title": "摩尔家族的房子",
        "subtitle": "长者角色带来经验、记忆与稳定生活节奏",
        "names": ["山姆", "詹妮弗"],
        "accent": "#566D8C",
    },
    {
        "title": "艺术家共居空间",
        "subtitle": "创作者住在同一生活单元，容易形成日常互动",
        "names": ["弗朗西斯科", "海莉", "拉吉夫", "拉托亚", "阿比盖尔"],
        "accent": "#7A5C98",
    },
    {
        "title": "独立住所",
        "subtitle": "个人住所中的工程师、律师、哲学家、诗人与数学家",
        "names": ["卡洛斯", "乔治", "瑞恩", "山本百合子", "亚当"],
        "accent": "#4F6F52",
    },
]


@dataclass(frozen=True)
class AgentCard:
    name: str
    age: int
    innate: str
    room: str
    portrait_path: Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    return right - left


def wrap_tokens(
    draw: ImageDraw.ImageDraw,
    tokens: list[str],
    separator: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = token if not current else f"{current}{separator}{token}"
        if text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = token
    if current:
        lines.append(current)
    return lines


def load_personas() -> list[str]:
    tree = ast.parse(START_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "personas":
                    return list(ast.literal_eval(node.value))
    raise ValueError(f"Cannot find personas in {START_PATH}")


def load_agent(name: str) -> AgentCard:
    agent_path = AGENTS_DIR / name / "agent.json"
    portrait_path = AGENTS_DIR / name / "portrait.png"
    with agent_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    scratch = data["scratch"]
    living_area = data["spatial"]["address"]["living_area"]
    return AgentCard(
        name=data["name"],
        age=int(scratch["age"]),
        innate=scratch["innate"],
        room=living_area[-1],
        portrait_path=portrait_path,
    )


def validate_groups(personas: list[str]) -> None:
    grouped = [name for group in GROUPS for name in group["names"]]
    missing = [name for name in personas if name not in grouped]
    extras = [name for name in grouped if name not in personas]
    if missing or extras:
        raise ValueError(f"Group names do not match start.py personas. missing={missing}, extras={extras}")


def normalize_traits(text: str) -> list[str]:
    traits = [item.strip() for item in re.split(r"[、，,]", text) if item.strip()]
    return traits or [text.strip()]


def draw_card(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    card: AgentCard,
    x: int,
    y: int,
    accent: str,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    draw.rounded_rectangle((x, y, x + CARD_W, y + CARD_H), radius=10, fill="#FFFFFF", outline="#D9D5C8", width=1)
    draw.rounded_rectangle((x, y, x + 8, y + CARD_H), radius=4, fill=accent)

    portrait = Image.open(card.portrait_path).convert("RGBA").resize((64, 64), Image.Resampling.NEAREST)
    portrait_x = x + 24
    portrait_y = y + 24
    draw.rounded_rectangle(
        (portrait_x - 7, portrait_y - 7, portrait_x + 71, portrait_y + 71),
        radius=12,
        fill="#EFEAE0",
        outline="#C9C0AE",
        width=1,
    )
    canvas.paste(portrait, (portrait_x, portrait_y), portrait)

    text_x = x + 112
    name_line = f"{card.name}  {card.age} 岁"
    draw.text((text_x, y + 17), name_line, fill="#242424", font=fonts["card_title"])

    traits = normalize_traits(card.innate)
    trait_lines = wrap_tokens(draw, traits, " / ", fonts["card_body"], CARD_W - 132)
    for index, line in enumerate(trait_lines[:2]):
        draw.text((text_x, y + 48 + index * 24), line, fill="#434343", font=fonts["card_body"])

    room_y = y + 82 if len(trait_lines) <= 1 else y + 84
    draw.text((text_x, room_y), f"房间：{card.room}", fill="#6A6257", font=fonts["card_small"])


def draw_group(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    group: dict,
    cards: dict[str, AgentCard],
    x: int,
    y: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    names = group["names"]
    rows = (len(names) + 1) // 2
    block_h = HEADER_H + BLOCK_PAD + rows * CARD_H + (rows - 1) * CARD_GAP + BLOCK_PAD
    accent = group["accent"]

    draw.rounded_rectangle((x, y, x + BLOCK_W, y + block_h), radius=18, fill="#F9F7F0", outline="#D4CFBF", width=2)
    draw.rounded_rectangle((x, y, x + BLOCK_W, y + HEADER_H), radius=18, fill=accent)
    draw.rectangle((x, y + HEADER_H - 18, x + BLOCK_W, y + HEADER_H), fill=accent)

    draw.text((x + 26, y + 18), group["title"], fill="#FFFFFF", font=fonts["group_title"])
    draw.text((x + 26, y + 52), group["subtitle"], fill="#F5F1E7", font=fonts["group_subtitle"])

    card_start_y = y + HEADER_H + BLOCK_PAD
    for index, name in enumerate(names):
        col = index % 2
        row = index // 2
        card_x = x + BLOCK_PAD + col * (CARD_W + CARD_GAP)
        card_y = card_start_y + row * (CARD_H + CARD_GAP)
        draw_card(canvas, draw, cards[name], card_x, card_y, accent, fonts)

    return block_h


def group_height(group: dict) -> int:
    rows = (len(group["names"]) + 1) // 2
    return HEADER_H + BLOCK_PAD + rows * CARD_H + (rows - 1) * CARD_GAP + BLOCK_PAD


def main() -> int:
    personas = load_personas()
    validate_groups(personas)
    cards = {name: load_agent(name) for name in personas}

    fonts = {
        "title": load_font(38, bold=True),
        "subtitle": load_font(22),
        "source": load_font(18),
        "group_title": load_font(24, bold=True),
        "group_subtitle": load_font(17),
        "card_title": load_font(22, bold=True),
        "card_body": load_font(18),
        "card_small": load_font(15),
    }

    body_start_y = TOP + 126
    left_h = sum(group_height(group) for group in GROUPS[:4]) + BLOCK_GAP_Y * (len(GROUPS[:4]) - 1)
    right_h = sum(group_height(group) for group in GROUPS[4:]) + BLOCK_GAP_Y * (len(GROUPS[4:]) - 1)
    canvas_h = body_start_y + max(left_h, right_h) + 94

    canvas = Image.new("RGB", (CANVAS_W, canvas_h), "#F2EFE7")
    draw = ImageDraw.Draw(canvas)

    draw.text((MARGIN_X, TOP), "25 个智能体 Agent 的角色群像", fill="#242424", font=fonts["title"])
    draw.text(
        (MARGIN_X, TOP + 54),
        "从 start.py 的 personas、每个角色的 agent.json 和 portrait.png 生成：先看生活单元，再看年龄、性格底色与初始房间。",
        fill="#555149",
        font=fonts["subtitle"],
    )
    draw.text(
        (MARGIN_X, canvas_h - 46),
        "数据来源：generative_agents/start.py；frontend/static/assets/village/agents/<角色名>/agent.json；portrait.png",
        fill="#777066",
        font=fonts["source"],
    )

    columns = [
        (MARGIN_X, TOP + 126, GROUPS[:4]),
        (MARGIN_X + BLOCK_W + BLOCK_GAP_X, TOP + 126, GROUPS[4:]),
    ]

    for x, start_y, groups in columns:
        y = start_y
        for group in groups:
            block_h = draw_group(canvas, draw, group, cards, x, y, fonts)
            y += block_h + BLOCK_GAP_Y

    ASSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(ASSET_PATH)

    print(f"已生成角色群像图: {ASSET_PATH.relative_to(ROOT)}")
    print(f"角色数量: {len(personas)}")
    print(f"生活单元分组: {len(GROUPS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
