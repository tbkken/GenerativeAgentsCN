#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate evidence-rich figures for chapters 24-38.

The figures are raster evidence boards, not Mermaid replacements. They use
project artifacts that readers can find locally: real portraits, real map
tiles, real config snippets, real checkpoint/compressed outputs, and concrete
upgrade fields discussed in the manuscript.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
GA = ROOT / "generative_agents"
ASSET_ROOT = ROOT / "docs" / "book" / "assets"
AGENTS_DIR = GA / "frontend" / "static" / "assets" / "village" / "agents"
HELPER_PATH = ROOT / "docs" / "book" / "scaffolds" / "part_03" / "ch17_23_mechanism_figures.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = load_module(HELPER_PATH, "part03_figure_helpers_for_part04_05")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def agent_json(name: str) -> dict:
    return load_json(AGENTS_DIR / name / "agent.json")


def currently(data: dict) -> str:
    return data.get("currently") or data.get("scratch", {}).get("currently", "")


def one_line(text: str, limit: int = 86) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: str, width: int = 4) -> None:
    draw.line([start, end], fill=fill, width=width)
    ex, ey = end
    sx, sy = start
    dx = 1 if ex >= sx else -1
    draw.polygon([(ex, ey), (ex - 16 * dx, ey - 9), (ex - 16 * dx, ey + 9)], fill=fill)


def draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: str) -> None:
    x, y = xy
    w, h = text_size(draw, text, H.FONT["small"])
    draw.rounded_rectangle([x, y, x + w + 22, y + 28], radius=14, fill=fill)
    draw.text((x + 11, y + 6), text, fill="#ffffff", font=H.FONT["small"])


def draw_project_footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    H.draw_wrapped(draw, 48, 832, text, H.FONT["h"], H.COLORS["ink"], 1460, max_lines=2)


def draw_portrait_row(image: Image.Image, names: list[str], x: int, y: int, title: str) -> None:
    draw = ImageDraw.Draw(image)
    draw.text((x, y), title, fill=H.COLORS["ink"], font=H.FONT["h"])
    px = x
    for name in names:
        box = (px, y + 42, px + 132, y + 194)
        x1, y1, x2, y2 = box
        draw.rounded_rectangle([x1, y1, x2, y2], radius=8, fill="#ffffff", outline="#d7c9b9", width=2)
        portrait_path = AGENTS_DIR / name / "portrait.png"
        if portrait_path.exists():
            portrait = Image.open(portrait_path).convert("RGBA").resize((86, 86), H.RESAMPLE_NEAREST)
            image.alpha_composite(portrait, (x1 + 23, y1 + 20))
        draw.text((x1 + 12, y2 - 32), name, fill=H.COLORS["ink"], font=H.FONT["body_bold"])
        px += 136


def draw_mini_json(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, data: dict, keys: list[str], color: str) -> None:
    lines = ["{"]
    for key in keys:
        value = data
        for part in key.split("."):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        lines.append(f'  "{key}": {json.dumps(value, ensure_ascii=False)[:76]},')
    lines.append("}")
    H.draw_window(draw, box, title, lines, color, max_lines=12)


def compressed_stats(name: str) -> dict:
    movement_path = GA / "results" / "compressed" / name / "movement.json"
    simulation_path = GA / "results" / "compressed" / name / "simulation.md"
    movement = load_json(movement_path)
    frames = [k for k in movement.get("all_movement", {}) if k.isdigit()]
    non_empty = [k for k in frames if movement["all_movement"][k]]
    sim_lines = [line for line in read_text(simulation_path).splitlines() if line.strip()]
    return {
        "movement": movement,
        "frames": len(frames),
        "non_empty": len(non_empty),
        "agents": list(movement.get("persona_init_pos", {}).keys()),
        "sim_lines": sim_lines,
    }


def path_map(
    image: Image.Image,
    box: tuple[int, int, int, int],
    sim_name: str,
    agents: list[tuple[str, str]],
    title: str,
    subtitle: str,
) -> None:
    maze = H.build_maze()
    overlays = []
    focus: set[tuple[int, int]] = set()
    labels = []
    for agent_name, color in agents:
        points = H.collect_path_points(sim_name, agent_name, limit=100)
        if not points:
            continue
        focus.update(points)
        overlays.append({"coords": points, "fill": color + "CC", "style": "line", "width": 6})
        overlays.append({"coords": {points[0], points[-1]}, "fill": color + "E8", "outline": "#ffffff", "style": "ellipse", "inset": 3, "width": 3})
        labels.append((points[0], f"{agent_name} start", color))
        labels.append((points[-1], f"{agent_name} end", color))
    if not focus:
        focus = {(72, 14), (118, 61)}
    H.render_map_panel(image, box, maze, focus, overlays, title, subtitle, labels)


def chapter24() -> Path:
    image, draw = H.base_canvas(
        "图 24-2：情人节派对传播的真实证据桌面",
        "用 book-party-pair 的本地输出把地图轨迹、压缩时间线、回放帧和证据边界放在一张图里。",
    )
    path_map(
        image,
        (46, 126, 930, 796),
        "book-party-pair",
        [("伊莎贝拉", H.COLORS["red"]), ("阿伊莎", H.COLORS["blue"])],
        "真实移动回放 movement.json 轨迹",
        "红线=伊莎贝拉，蓝线=阿伊莎；这张图展示小样本派对实验的可核查证据边界。",
    )
    stats = compressed_stats("book-party-pair")
    checkpoint_count = len(list((GA / "results" / "checkpoints" / "book-party-pair").glob("simulate-*.json")))
    H.draw_card(
        draw,
        (970, 132, 1548, 318),
        "真实产物",
        [
            f"断点 checkpoint JSON：{checkpoint_count} 个",
            f"移动回放 movement frames：{stats['frames']} 帧",
            f"非空帧 non-empty frames：{stats['non_empty']} 帧",
            "对话记录 conversation.json：当前样本为空，不能伪称传播成功。",
        ],
        H.COLORS["purple"],
    )
    H.draw_window(
        draw,
        (970, 350, 1548, 558),
        "时间线 simulation.md 摘录",
        stats["sim_lines"][:8],
        "#fde68a",
        max_lines=8,
    )
    H.draw_card(
        draw,
        (970, 590, 1548, 796),
        "读图判断",
        [
            "地图证明角色在哪里移动。",
            "时间线证明这一刻做了什么。",
            "conversation 为空时，只能说“运行产生了轨迹”，不能说“信息完成传播”。",
        ],
        H.COLORS["teal"],
    )
    draw_project_footer(draw, "读法：派对实验的结论必须同时回到时间线 simulation.md、对话记录 conversation.json、移动回放 movement.json 和断点 checkpoint；缺一项，结论就要降级。")
    return H.save(image, 24, "ch24_party_evidence_board.png")


def chapter25() -> Path:
    image, draw = H.base_canvas(
        "图 25-2：山姆竞选实验的角色与证据设计板",
        "竞选实验先看角色设定，再看对话证据；没有真实传播路径，就不能把“知道竞选”当成事实。",
    )
    draw_portrait_row(image, ["山姆", "约翰", "汤姆", "伊莎贝拉"], 58, 128, "参与竞选传播的关键角色")
    sam = agent_json("山姆")
    tom = agent_json("汤姆")
    john = agent_json("约翰")
    H.draw_card(
        draw,
        (58, 386, 510, 640),
        "山姆 Sam：竞选源头",
        [
            one_line(currently(sam), 98),
            "实验中竞选信息必须从山姆或明确上游对话出现。",
        ],
        H.COLORS["purple"],
    )
    H.draw_card(
        draw,
        (548, 386, 1000, 640),
        "汤姆 Tom：反对态度",
        [
            one_line(currently(tom), 98),
            "他不喜欢山姆，是评价态度分化的重要角色。",
        ],
        H.COLORS["red"],
    )
    H.draw_card(
        draw,
        (1038, 386, 1490, 640),
        "约翰 John：公共传播节点",
        [
            one_line(currently(john), 98),
            "他会询问镇长选举，适合作为公共话题触发点。",
        ],
        H.COLORS["teal"],
    )
    H.draw_window(
        draw,
        (58, 674, 1490, 800),
        "证据链 evidence chain",
        [
            "1. agent.json：谁初始知道竞选，谁对山姆有态度。",
            "2. conversation.json：山姆是否真的把竞选告诉别人。",
            "3. simulation.md：谁在什么时间、什么地点提到竞选。",
            "4. checkpoint memory：后续角色是否能回忆起竞选事实。",
        ],
        "#bfdbfe",
        max_lines=5,
    )
    draw_project_footer(draw, "读法：竞选实验不是“有人提到 election”就算成功；它要看源头、传播路径、态度差异和后续记忆，所有结论都要落回项目文件。")
    return H.save(image, 25, "ch25_election_evidence_board.png")


def chapter26() -> Path:
    image, draw = H.base_canvas(
        "图 26-2：自定义事件如何进入小镇运行证据",
        "以 book-config-ai-seminar 为样本，观察 currently 设定、真实位置、时间线和对话文件之间的关系。",
    )
    path_map(
        image,
        (46, 126, 860, 770),
        "book-config-ai-seminar",
        [("阿伊莎", H.COLORS["blue"]), ("克劳斯", H.COLORS["purple"])],
        "AI 研讨会样本的真实小镇位置",
        "角色先在地图上行动，事件才可能通过对话、记忆和日程进入证据链。",
    )
    stats = compressed_stats("book-config-ai-seminar")
    ck = load_json(GA / "results" / "checkpoints" / "book-config-ai-seminar" / "simulate-20240213-1000.json")
    H.draw_card(
        draw,
        (900, 132, 1548, 328),
        "运行对象",
        [
            "实验名：book-config-ai-seminar",
            "角色：阿伊莎 / 克劳斯",
            f"step={ck.get('step')}, stride={ck.get('stride')}",
            f"移动帧 movement frames={stats['frames']}",
        ],
        H.COLORS["purple"],
    )
    H.draw_window(
        draw,
        (900, 360, 1548, 570),
        "时间线 simulation.md 摘录",
        stats["sim_lines"][:8],
        "#fde68a",
        max_lines=8,
    )
    H.draw_window(
        draw,
        (900, 602, 1548, 770),
        "自定义事件 evidence checklist",
        [
            "currently 是否写入角色状态",
            "schedule 是否围绕事件发生变化",
            "conversation 是否出现事件转述",
            "movement 是否落到合理地点",
        ],
        "#a7f3d0",
        max_lines=6,
    )
    draw_project_footer(draw, "读法：自定义事件不是把一句话写进 currently 就结束；它必须穿过日程 schedule、对话 conversation、记忆 memory 和移动回放 movement，才算进入仿真。")
    return H.save(image, 26, "ch26_custom_event_evidence_board.png")


def chapter27() -> Path:
    image, draw = H.base_canvas(
        "图 27-2：扩展角色、关系和地点时要同时看四层项目文件",
        "新增内容不能只改一个 JSON 字段；角色、空间记忆、地图资源和提示词语义要互相对齐。",
    )
    draw_portrait_row(image, ["玛丽亚", "克劳斯", "山姆", "汤姆", "伊莎贝拉"], 58, 126, "扩展关系与事件时常用的原始角色")
    H.draw_card(
        draw,
        (58, 390, 390, 638),
        "角色层 agent.json",
        [
            "scratch：年龄、性格、当前状态 currently。",
            "spatial：角色知道哪些地点。",
            "schedule：日程如何被事件影响。",
        ],
        H.COLORS["purple"],
    )
    H.draw_card(
        draw,
        (430, 390, 762, 638),
        "地图层 maze.json",
        [
            "world / sector / arena / game_object 构成完整地址。",
            "新增地点必须能被后端世界模型找到。",
        ],
        H.COLORS["teal"],
    )
    H.draw_card(
        draw,
        (802, 390, 1134, 638),
        "前端层 tilemap",
        [
            "瓦片地图 tile map 决定读者能看见什么。",
            "只有后端地址、没有前端外观，会产生视觉断层。",
        ],
        H.COLORS["orange"],
    )
    H.draw_card(
        draw,
        (1174, 390, 1506, 638),
        "语言层 prompt",
        [
            "地点和对象名称要能被模型理解。",
            "角色关系变化要进入对话提示词 prompt 和记忆检索。",
        ],
        H.COLORS["red"],
    )
    draw_arrow(draw, (390, 708), (430, 708), H.COLORS["purple"])
    draw_arrow(draw, (762, 708), (802, 708), H.COLORS["teal"])
    draw_arrow(draw, (1134, 708), (1174, 708), H.COLORS["orange"])
    H.draw_window(
        draw,
        (58, 674, 1506, 798),
        "扩展检查清单",
        [
            "新增关系：先改 agent.json / memory，再用 conversation 验证是否影响对话。",
            "新增地点：后端 maze.json、前端 tilemap、角色 spatial.tree、prompt 语义必须一致。",
            "新增角色：头像、agent.json、初始位置、空间记忆、日程和运行名单都要完整。",
        ],
        "#bfdbfe",
        max_lines=5,
    )
    draw_project_footer(draw, "读法：扩展实验越靠近地图和资源层，越容易出现“后端知道、前端看不到”或“角色能说、地图不能走”的断层。")
    return H.save(image, 27, "ch27_extension_layers_board.png")


def chapter28() -> Path:
    image, draw = H.base_canvas(
        "图 28-2：模型配置与运行稳定性观察台",
        "中文本地模型或远程模型实验，第一眼要看 provider、model、embedding、S/F/R 和结构化输出边界。",
    )
    config = load_json(GA / "data" / "config.json")
    draw_mini_json(
        draw,
        (58, 128, 760, 482),
        "真实配置 generative_agents/data/config.json",
        config,
        ["agent.think.llm.provider", "agent.think.llm.model", "agent.associate.embedding.provider", "agent.associate.embedding.model", "agent.think.poignancy_max"],
        "#bfdbfe",
    )
    log_path = GA / "results" / "checkpoints" / "book-smoke" / "book-smoke.log"
    log_lines = [line.strip() for line in read_text(log_path).splitlines() if line.strip()][:12] if log_path.exists() else ["log file not found"]
    H.draw_window(draw, (798, 128, 1548, 482), "真实日志 book-smoke.log 摘录", log_lines, "#d9f99d", max_lines=10)
    H.draw_card(
        draw,
        (58, 522, 482, 780),
        "先看模型接口 provider",
        [
            "provider 决定请求协议、鉴权、失败形态。",
            "同名模型在不同服务端可能行为不同。",
        ],
        H.COLORS["purple"],
    )
    H.draw_card(
        draw,
        (520, 522, 944, 780),
        "再看结构化输出 JSON",
        [
            "日程、地点、行动选择都依赖可解析输出。",
            "JSON 失败会直接破坏后续仿真链路。",
        ],
        H.COLORS["orange"],
    )
    H.draw_card(
        draw,
        (982, 522, 1548, 780),
        "最后看隐藏成本",
        [
            "S/F/R 表示成功数、失败数和请求数。",
            "传播率提升但调用暴涨，不一定是好升级。",
        ],
        H.COLORS["teal"],
    )
    draw_project_footer(draw, "读法：模型实验不是换一个模型名就结束；配置文件、日志、结构化输出和成本摘要共同决定这次仿真是否可信。")
    return H.save(image, 28, "ch28_model_config_observatory.png")


def chapter29() -> Path:
    image, draw = H.base_canvas(
        "图 29-2：可信行为评价的六层证据桌",
        "把 persona、memory、schedule、dialogue、diffusion 和 movement 摆在同一张工作台上，避免只凭对话流畅度下结论。",
    )
    labels = [
        ("身份 persona", "agent.json / scratch", H.COLORS["purple"]),
        ("记忆 memory", "checkpoint / conversation", H.COLORS["blue"]),
        ("计划 schedule", "daily_schedule / action", H.COLORS["teal"]),
        ("反应 reacting", "chat / wait / replan", H.COLORS["orange"]),
        ("传播 diffusion", "conversation path", H.COLORS["red"]),
        ("落地 movement", "movement.json / replay", H.COLORS["green"]),
    ]
    for i, (title, body, color) in enumerate(labels):
        x = 58 + (i % 3) * 500
        y = 132 + (i // 3) * 220
        H.draw_card(draw, (x, y, x + 438, y + 170), title, [body, "必须有可追溯文件，不能只写主观评价。"], color)
    path_map(
        image,
        (58, 588, 740, 800),
        "book-party-pair",
        [("伊莎贝拉", H.COLORS["red"]), ("阿伊莎", H.COLORS["blue"])],
        "行动落地样本 movement",
        "语言承诺是否真的变成位置变化，要回到移动回放。",
    )
    H.draw_window(
        draw,
        (780, 588, 1548, 800),
        "可信评价 verdict template",
        [
            "结论：传播证据弱 / 中 / 强",
            "证据：simulation + conversation + checkpoint + movement",
            "失败类型：no_contact / memory_miss / plan_not_updated / movement_miss",
            "下一步：定位到 prompt、检索、日程或空间路径。",
        ],
        "#fde68a",
        max_lines=6,
    )
    draw_project_footer(draw, "读法：可信行为不是一个分数，而是一条证据链。缺少地图和文件证据时，对话再自然也只能算弱结论。")
    return H.save(image, 29, "ch29_credible_behavior_evidence_desk.png")


def chapter30() -> Path:
    image, draw = H.base_canvas(
        "图 30-2：风险审计不是原则清单，而是证据回查现场",
        "幻觉、过度合作、偏差和不可复现，都要回到日志、prompt、checkpoint 和回放材料中定位。",
    )
    H.draw_window(
        draw,
        (58, 128, 740, 374),
        "风险样例 risk sample",
        [
            "角色声称参加了派对，但 movement.json 没有到场。",
            "角色提到从未发生过的对话。",
            "所有角色无条件接受任务，失去社会可信度。",
            "同一配置多次运行结果差异很大。",
        ],
        "#fecaca",
        max_lines=7,
    )
    H.draw_card(
        draw,
        (790, 128, 1548, 374),
        "证据回查路径",
        [
            "simulation.md：先定位故事片段。",
            "conversation.json：再核验说话双方和原话。",
            "checkpoint：检查记忆、日程和行动状态。",
            "movement.json：确认位置和到场。",
        ],
        H.COLORS["purple"],
    )
    risks = [
        ("记忆幻觉 memory hallucination", "回查 conversation 与 checkpoint"),
        ("过度合作 over-cooperation", "保留拒绝、误解和冲突样例"),
        ("模型漂移 model drift", "记录 provider、model、prompt 版本"),
        ("选择性展示 cherry-picking", "报告多次运行方差"),
    ]
    for i, (title, body) in enumerate(risks):
        x = 58 + (i % 2) * 744
        y = 418 + (i // 2) * 170
        H.draw_card(draw, (x, y, x + 700, y + 132), title, [body], [H.COLORS["red"], H.COLORS["orange"], H.COLORS["blue"], H.COLORS["teal"]][i])
    draw_project_footer(draw, "读法：风险章节最怕停在价值判断。每个风险都要对应一条项目内证据路径，才能让读者知道怎样发现、怎样复查、怎样修。")
    return H.save(image, 30, "ch30_risk_audit_workbench.png")


def chapter31() -> Path:
    image, draw = H.base_canvas(
        "图 31-2：2023-2026 前沿方向如何落回 Generative Agents 项目",
        "前沿不是新名词列表，而是把记忆、反思、规划、协作、仿真和评价重新接回小镇文件。",
    )
    lanes = [
        ("长期记忆 memory governance", "associate.py / storage / vector store", H.COLORS["purple"]),
        ("反思学习 reflexion-style learning", "reflect_insights.txt / lesson / skill", H.COLORS["blue"]),
        ("目标规划 goal planning", "schedule.py / Agent.make_plan()", H.COLORS["teal"]),
        ("多智能体协作 multi-agent collaboration", "conversation.json / shared task", H.COLORS["orange"]),
        ("社会仿真 social simulation", "movement.json / simulation.md / batch runs", H.COLORS["green"]),
        ("评价体系 evaluation", "metrics.json / report.md / cost summary", H.COLORS["red"]),
    ]
    for i, (title, body, color) in enumerate(lanes):
        x = 70 + (i % 2) * 740
        y = 128 + (i // 2) * 180
        H.draw_card(draw, (x, y, x + 690, y + 132), title, [body, "升级必须能被本地项目材料验证。"], color)
    draw_project_footer(draw, "读法：第五部分的每条前沿路线都要回到当前小镇项目。不能把 Generative Agents 改成另一套系统，也不能只介绍论文概念。")
    return H.save(image, 31, "ch31_frontier_to_project_map.png")


def chapter32() -> Path:
    image, draw = H.base_canvas(
        "图 32-2：长期记忆治理的真实存储剖面",
        "长期记忆不是“多存一点”，而是围绕来源、类型、冲突、摘要和检索策略治理 memory stream。",
    )
    storage = GA / "results" / "checkpoints" / "book-smoke" / "storage" / "阿伊莎" / "associate"
    files = [p.name for p in sorted(storage.glob("*.json"))]
    H.draw_window(draw, (58, 130, 650, 468), "真实存储目录 storage/阿伊莎/associate", files, "#bfdbfe", max_lines=12)
    assoc = load_json(GA / "data" / "config.json")["agent"]["associate"]
    H.draw_card(
        draw,
        (700, 130, 1548, 298),
        "当前检索配置 associate",
        [
            f"embedding provider：{assoc['embedding']['provider']}",
            f"embedding model：{assoc['embedding']['model']}",
            f"retention：{assoc['retention']}",
        ],
        H.COLORS["purple"],
    )
    memory_types = [
        ("relationship", "谁和谁是什么关系，证据来自哪段对话。"),
        ("goal", "当前目标、成功标准、进度和过期时间。"),
        ("summary", "跨天摘要，降低检索噪声。"),
        ("skill", "失败复盘后留下的可复用策略。"),
    ]
    for i, (title, body) in enumerate(memory_types):
        x = 700 + (i % 2) * 426
        y = 330 + (i // 2) * 170
        H.draw_card(draw, (x, y, x + 386, y + 132), title, [body], [H.COLORS["teal"], H.COLORS["orange"], H.COLORS["blue"], H.COLORS["red"]][i])
    H.draw_window(
        draw,
        (58, 510, 650, 790),
        "治理字段 governance fields",
        [
            '"source_type": "conversation"',
            '"source_id": "conversation_20240214_1030"',
            '"confidence": 0.82',
            '"last_verified_at": "2024-02-14T17:00:00"',
            '"conflict_with": ["memory_x"]',
        ],
        "#fde68a",
        max_lines=10,
    )
    draw_project_footer(draw, "读法：记忆升级的重点不是无限保存，而是让每条记忆能说明来源、类型、可信度和下游用途。")
    return H.save(image, 32, "ch32_memory_governance_board.png")


def chapter33() -> Path:
    image, draw = H.base_canvas(
        "图 33-2：从失败结果到可复用技能记忆",
        "反思升级要让 outcome、self-evaluation、lesson 和 skill 回到后续行动，而不是只多写一段总结。",
    )
    H.draw_card(
        draw,
        (58, 132, 394, 364),
        "行动 action",
        ["伊莎贝拉邀请居民参加派对。", "证据来自 conversation.json 和 simulation.md。"],
        H.COLORS["purple"],
    )
    H.draw_card(
        draw,
        (438, 132, 774, 364),
        "结果 outcome",
        ["对方知道派对但婉拒，或没有在 17:00 到场。", "结果要绑定 movement.json。"],
        H.COLORS["red"],
    )
    H.draw_card(
        draw,
        (818, 132, 1154, 364),
        "自评 self-evaluation",
        ["失败原因是时间冲突、关系弱，还是地点不合理。", "不要让模型泛泛自责。"],
        H.COLORS["orange"],
    )
    H.draw_card(
        draw,
        (1198, 132, 1534, 364),
        "技能 skill",
        ["下次先确认时间，再邀请；对熟人用更具体的理由。", "写入可检索记忆。"],
        H.COLORS["teal"],
    )
    for x in [394, 774, 1154]:
        draw_arrow(draw, (x + 8, 248), (x + 36, 248), H.COLORS["ink"])
    H.draw_window(
        draw,
        (58, 418, 760, 790),
        "可新增提示词 prompt",
        [
            "self_evaluate_action.txt",
            "derive_lesson_from_failure.txt",
            "retrieve_relevant_skill.txt",
            "revise_future_strategy.txt",
            "",
            "输入必须带 evidence：对话、位置、日程和结果标签。",
        ],
        "#bfdbfe",
        max_lines=12,
    )
    H.draw_window(
        draw,
        (800, 418, 1534, 790),
        "技能记忆 skill memory 示例",
        [
            "{",
            '  "trigger": "邀请活动失败",',
            '  "lesson": "先确认对方时间，再给出具体理由",',
            '  "evidence": ["conversation_...", "movement_..."],',
            '  "reuse_for": ["party", "seminar", "team_task"]',
            "}",
        ],
        "#d9f99d",
        max_lines=10,
    )
    draw_project_footer(draw, "读法：Reflexion 风格升级的核心是“失败证据 -> 经验 -> 可复用策略 -> 下次行动”，不是把 reflection 写得更长。")
    return H.save(image, 33, "ch33_reflexion_skill_board.png")


def chapter34() -> Path:
    image, draw = H.base_canvas(
        "图 34-2：目标驱动规划在小镇里怎样落地",
        "显式目标 active goal 要进入日程、候选行动、进度评估和证据报告，而不是替代原有日常生活。",
    )
    ck = load_json(GA / "results" / "checkpoints" / "book-party-pair" / "simulate-20240214-0800.json")
    schedule = ck["agents"]["伊莎贝拉"]["schedule"]["daily_schedule"]
    H.draw_card(
        draw,
        (58, 132, 558, 318),
        "目标 active goal",
        [
            "17:00 前让至少三位居民知道派对。",
            "至少两人表示愿意参加。",
            "目标只在相关事件中启用，不能吞掉日常生活。",
        ],
        H.COLORS["purple"],
    )
    H.draw_window(
        draw,
        (600, 132, 1540, 318),
        "真实日程 daily_schedule 摘录",
        [f"{item['start']//60:02d}:{item['start']%60:02d} {item['describe']} ({item['duration']}m)" for item in schedule[:7]],
        "#fde68a",
        max_lines=8,
    )
    steps = [
        ("候选行动 candidate actions", "邀请玛丽亚 / 去咖啡馆 / 准备饮品"),
        ("目标贡献 goal contribution", "这个行动是否增加传播覆盖率"),
        ("自然性 naturalness", "是否符合伊莎贝拉当前身份和日程"),
        ("进度 progress", "已邀请几人、几人接受、几人到场"),
    ]
    for i, (title, body) in enumerate(steps):
        x = 58 + (i % 2) * 740
        y = 374 + (i // 2) * 168
        H.draw_card(draw, (x, y, x + 690, y + 126), title, [body], [H.COLORS["teal"], H.COLORS["orange"], H.COLORS["blue"], H.COLORS["green"]][i])
    draw_project_footer(draw, "读法：目标规划的价值要用 conversation 和 movement 验证：邀请是否更有针对性，接受是否增加，到场是否真的发生。")
    return H.save(image, 34, "ch34_goal_planning_dashboard.png")


def chapter35() -> Path:
    image, draw = H.base_canvas(
        "图 35-2：情人节派对从个人事件升级成协作事件",
        "公共事件板 event board 把自然对话、角色分工、任务状态和协作证据连接起来。",
    )
    draw_portrait_row(image, ["伊莎贝拉", "埃迪", "玛丽亚", "克劳斯", "亚当"], 58, 126, "协作实验角色")
    event_board = [
        '"event_id": "valentine_party"',
        '"owner": "伊莎贝拉"',
        '"time": "2024-02-14 17:00"',
        '"location": "霍布斯咖啡馆"',
        '"status": "active"',
    ]
    H.draw_window(draw, (58, 380, 596, 790), "公共事件板 event board", ["{", *["  " + line + "," for line in event_board], "}"], "#bfdbfe", max_lines=10)
    tasks = [
        ("准备饮品", "伊莎贝拉", "active"),
        ("确认音乐", "埃迪", "todo"),
        ("邀请顾客", "玛丽亚", "accepted"),
        ("17:00 布置咖啡馆", "克劳斯", "pending"),
    ]
    for i, (task, owner, status) in enumerate(tasks):
        x = 646 + (i % 2) * 430
        y = 386 + (i // 2) * 170
        H.draw_card(draw, (x, y, x + 386, y + 128), task, [f"assignee：{owner}", f"status：{status}"], [H.COLORS["purple"], H.COLORS["teal"], H.COLORS["orange"], H.COLORS["blue"]][i])
    H.draw_window(
        draw,
        (646, 720, 1506, 790),
        "状态更新链路",
        ["自然对话 conversation -> 结构化抽取 dialogue act -> 公共事件板 event board -> 报告 report"],
        "#d9f99d",
        max_lines=2,
    )
    draw_project_footer(draw, "读法：协作升级不是让所有人自动合作，而是让接受、拒绝、遗忘和冲突都能被记录到事件板并回到证据文件。")
    return H.save(image, 35, "ch35_collaboration_event_board.png")


def chapter36() -> Path:
    image, draw = H.base_canvas(
        "图 36-2：从单次故事到批量社会仿真实验台",
        "批量实验要保留每次运行目录，再把传播、到场和方差写进可比较结果。",
    )
    runs = ["book-smoke", "book-config-ai-seminar", "book-party-pair"]
    y = 138
    left_lines = [
        "python start.py --name <run> --step 72 --stride 10",
        "python compress.py --name <run>",
        "",
        *[f"results/checkpoints/{run}" for run in runs],
        *[f"results/compressed/{run}/movement.json" for run in runs],
        "",
        "每个 run 都要保留：",
        "- checkpoint JSON",
        "- conversation.json",
        "- simulation.md",
        "- movement.json",
        "- log / LLM summary",
    ]
    H.draw_window(
        draw,
        (58, 128, 700, 790),
        "批量实验 evidence package",
        left_lines,
        "#bfdbfe",
        max_lines=20,
    )
    draw.rounded_rectangle([742, 128, 1548, 790], radius=8, fill="#fffdf8", outline="#d7c9b9", width=2)
    draw.text((770, 150), "批量统计 batch summary", fill=H.COLORS["ink"], font=H.FONT["h"])
    headers = ["run", "agents", "frames", "non-empty", "simulation lines"]
    col_x = [770, 1040, 1160, 1284, 1420]
    for x, header in zip(col_x, headers):
        draw.text((x, 200), header, fill=H.COLORS["muted"], font=H.FONT["small"])
    for idx, run in enumerate(runs):
        stats = compressed_stats(run)
        row_y = 244 + idx * 82
        values = [run, str(len(stats["agents"])), str(stats["frames"]), str(stats["non_empty"]), str(len(stats["sim_lines"]))]
        for x, value in zip(col_x, values):
            draw.text((x, row_y), value, fill=H.COLORS["ink"], font=H.FONT["body"])
        draw.line([764, row_y + 44, 1518, row_y + 44], fill="#e0d5c8", width=1)
    H.draw_card(
        draw,
        (770, 548, 1518, 736),
        "批量实验的结论写法",
        [
            "不要只展示最好的一次 run。",
            "报告均值、最小值、最大值、失败样例和运行质量。",
            "传播成功率必须能回到 conversation 与 movement。",
        ],
        H.COLORS["purple"],
    )
    draw_project_footer(draw, "读法：社会仿真从故事走向实验的关键，是把每次运行保存成独立证据包，再统一统计和比较。")
    return H.save(image, 36, "ch36_batch_simulation_console.png")


def chapter37() -> Path:
    image, draw = H.base_canvas(
        "图 37-2：评价报告工作台",
        "metrics.json 给机器读，report.md 给人读；两者都必须链接回 conversation、movement、simulation 和 checkpoint。",
    )
    H.draw_window(
        draw,
        (58, 128, 744, 492),
        "指标文件 metrics.json 示例",
        [
            "{",
            '  "experiment": {"name": "book-party-small-run-01"},',
            '  "diffusion": {"unique_informed_agents": 4},',
            '  "attendance": {"attendance_count": 2},',
            '  "runtime": {"llm_requests": 320}',
            "}",
        ],
        "#bfdbfe",
        max_lines=10,
    )
    H.draw_window(
        draw,
        (790, 128, 1548, 492),
        "人工报告 report.md 结构",
        [
            "# 实验评价报告",
            "## 实验配置",
            "## 指标摘要",
            "## 传播路径",
            "## 到场统计",
            "## 失败样例",
            "## 成本与稳定性",
        ],
        "#fde68a",
        max_lines=10,
    )
    sources = [
        ("对话 conversation", "谁对谁说，在哪里说，说了什么。"),
        ("移动 movement", "角色是否真的到达目标地点。"),
        ("时间线 simulation", "快速定位故事片段。"),
        ("断点 checkpoint", "检查记忆、日程和 LLM summary。"),
    ]
    for i, (title, body) in enumerate(sources):
        x = 58 + (i % 2) * 732
        y = 536 + (i // 2) * 126
        H.draw_card(draw, (x, y, x + 690, y + 92), title, [body], [H.COLORS["purple"], H.COLORS["teal"], H.COLORS["orange"], H.COLORS["blue"]][i])
    draw_project_footer(draw, "读法：评价体系升级不是多算几个数字，而是让每个数字都能回到原始证据，失败样例也能被复查。")
    return H.save(image, 37, "ch37_evaluation_workbench.png")


def chapter38() -> Path:
    image, draw = H.base_canvas(
        "图 38-2：前沿升级路线的项目落地墙",
        "每一步都先保留默认系统基线，再沿着观测、记忆、反思、规划、协作和模型路由逐步推进。",
    )
    stages = [
        ("1 观测 evaluation", "metrics.json / report.md", H.COLORS["purple"]),
        ("2 记忆 memory", "relationship / source / conflict", H.COLORS["blue"]),
        ("3 反思 reflexion", "lesson / skill / reuse", H.COLORS["teal"]),
        ("4 目标 goal", "active goal / progress", H.COLORS["orange"]),
        ("5 协作 team", "event board / shared task", H.COLORS["green"]),
        ("6 路由 routing", "daily / reflection / planning model", H.COLORS["red"]),
    ]
    for i, (title, body, color) in enumerate(stages):
        x = 58 + (i % 3) * 500
        y = 128 + (i // 3) * 190
        H.draw_card(draw, (x, y, x + 438, y + 142), title, [body, "保留默认系统基线。"], color)
    H.draw_window(
        draw,
        (58, 526, 724, 792),
        "当前项目锚点 project anchors",
        [
            "generative_agents/modules/agent.py",
            "generative_agents/modules/memory/associate.py",
            "generative_agents/modules/memory/schedule.py",
            "generative_agents/data/prompts/*.txt",
            "generative_agents/results/checkpoints/<name>/",
            "generative_agents/results/compressed/<name>/movement.json",
            "generative_agents/results/compressed/<name>/simulation.md",
        ],
        "#bfdbfe",
        max_lines=12,
    )
    H.draw_window(
        draw,
        (770, 526, 1510, 792),
        "最终实践路径",
        [
            "两周：先做观测与评价增强。",
            "一个月：加入关系记忆。",
            "两到三个月：加入失败复盘和技能库。",
            "研究项目：再做目标规划与组织化协作。",
            "长期维护：同步做模型路由与成本优化。",
        ],
        "#d9f99d",
        max_lines=8,
    )
    draw_project_footer(draw, "读法：路线图的底线是“不另起炉灶”。所有前沿升级都沿着 Generative Agents 的原始模块继续生长。")
    return H.save(image, 38, "ch38_upgrade_roadmap_wall.png")


def main() -> int:
    paths = [
        chapter24(),
        chapter25(),
        chapter26(),
        chapter27(),
        chapter28(),
        chapter29(),
        chapter30(),
        chapter31(),
        chapter32(),
        chapter33(),
        chapter34(),
        chapter35(),
        chapter36(),
        chapter37(),
        chapter38(),
    ]
    for path in paths:
        print(f"generated: {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
