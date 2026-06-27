#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize prose terminology in part 04/05 to "中文 English".

The script edits only text outside fenced code blocks and outside inline-code
spans. It intentionally skips source/reference rows so paths and filenames stay
literal.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
FILES = list((ROOT / "docs/book/manuscript/part_04").glob("chapter_*.md")) + list(
    (ROOT / "docs/book/manuscript/part_05").glob("chapter_*.md")
)

PHRASE_REPLS = [
    ("persona/currently", "角色设定 persona / 当前状态 currently"),
    ("spatial grounding", "空间落地 spatial grounding"),
    ("reaction/dialogue", "反应 reaction / 对话 dialogue"),
    ("information diffusion", "信息扩散 information diffusion"),
    ("schedule revision", "日程修订 schedule revision"),
    ("replay/evaluation", "回放 replay / 评价 evaluation"),
    ("memory stream", "记忆流 memory stream"),
    ("relation memory", "关系记忆 relation memory"),
    ("chat memory", "聊天记忆 chat memory"),
    ("sandbox grounding", "沙盒落地 sandbox grounding"),
    ("multi-agent interaction", "多智能体互动 multi-agent interaction"),
    ("role-playing communicative agents", "角色扮演式沟通智能体 role-playing communicative agents"),
    ("multi agent conversation framework", "多智能体对话框架 multi-agent conversation framework"),
    ("LLM as agents", "大语言模型作为智能体 LLM as agents"),
    ("autonomous agents", "自主智能体 autonomous agents"),
    ("LLM-driven generative agents", "大语言模型驱动的生成式智能体 LLM-driven generative agents"),
    ("agent profile", "智能体画像 agent profile"),
    ("agent demo", "智能体 demo"),
    ("active goals", "主动目标 active goals"),
    ("goal decomposition", "目标分解 goal decomposition"),
    ("goal progress evaluation", "目标进度评估 goal progress evaluation"),
    ("goal_completion_rate", "目标完成率 goal_completion_rate"),
    ("goal_progress_accuracy", "目标进度准确率 goal_progress_accuracy"),
    ("unique_informed_agents", "知情角色数 unique_informed_agents"),
    ("multi_agent_credit_traceability", "多智能体归因可追踪性 multi_agent_credit_traceability"),
]

REGEX_REPLS = [
    (re.compile(r"(?<!智能体 )Agent 领域"), "智能体 Agent 领域"),
    (re.compile(r"(?<!智能体 )Agent 系统"), "智能体 Agent 系统"),
    (re.compile(r"(?<!智能体 )Agent 评价"), "智能体 Agent 评价"),
    (re.compile(r"(?<!智能体 )agent 行为"), "智能体 agent 行为"),
    (re.compile(r"(?<!智能体 )agent 数量"), "智能体 agent 数量"),
    (re.compile(r"(?<!智能体 )agent 能力"), "智能体 agent 能力"),
    (re.compile(r"(?<!智能体 )agent 的"), "智能体 agent 的"),
    (re.compile(r"一个 agent"), "一个智能体 agent"),
    (re.compile(r"很多 agent"), "很多智能体 agent"),
    (re.compile(r"(?<!规划 )planning"), "规划 planning"),
    (re.compile(r"(?<!对话 )dialogue"), "对话 dialogue"),
    (re.compile(r"(?<!反思 )reflection"), "反思 reflection"),
    (re.compile(r"(?<!检索 )retrieval"), "检索 retrieval"),
    (re.compile(r"(?<!反应 )reacting"), "反应 reacting"),
    (re.compile(r"(?<!日程 )schedule"), "日程 schedule"),
    (re.compile(r"(?<!提示词 )prompt"), "提示词 prompt"),
    (re.compile(r"(?<!断点 )checkpoint(?![\w./-])"), "断点 checkpoint"),
    (re.compile(r"(?<!对话记录 )conversation(?!\.json)"), "对话记录 conversation"),
    (re.compile(r"(?<!时间线 )simulation(?!\.md)"), "时间线 simulation"),
    (re.compile(r"(?<!移动回放 )movement(?!\.json)"), "移动回放 movement"),
    (re.compile(r"(?<!模型提供方 )provider"), "模型提供方 provider"),
    (re.compile(r"(?<!向量嵌入 )embedding"), "向量嵌入 embedding"),
    (re.compile(r"(?<!指标 )metrics(?!\.json)"), "指标 metrics"),
    (re.compile(r"(?<!报告 )report(?!\.md)"), "报告 report"),
    (re.compile(r"(?<!目标 )goal(?![_\w-])"), "目标 goal"),
    (re.compile(r"(?<!技能 )skill(?![_\w-])"), "技能 skill"),
    (re.compile(r"(?<!公共事件板 )event board"), "公共事件板 event board"),
    (re.compile(r"(?<!模型路由 )routing"), "模型路由 routing"),
    (re.compile(r"(?<!评价 )evaluation"), "评价 evaluation"),
]


def fix_segment(segment: str) -> str:
    for old, new in PHRASE_REPLS:
        segment = segment.replace(old, new)
    for pattern, new in REGEX_REPLS:
        segment = pattern.sub(new, segment)
    return segment


def fix_line(line: str) -> str:
    if line.startswith("- Local ") or line.startswith("|") or line.startswith("!["):
        return line
    parts = line.split("`")
    for index in range(0, len(parts), 2):
        parts[index] = fix_segment(parts[index])
    return "`".join(parts)


def main() -> int:
    changed = 0
    for path in FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        in_code = False
        for line in lines:
            if line.startswith("```"):
                in_code = not in_code
                out.append(line)
                continue
            out.append(line if in_code else fix_line(line))
        new_text = "\n".join(out) + "\n"
        old_text = path.read_text(encoding="utf-8")
        if new_text != old_text:
            path.write_text(new_text, encoding="utf-8")
            changed += 1
            print(f"normalized: {path.relative_to(ROOT).as_posix()}")
    print(f"changed_files={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
