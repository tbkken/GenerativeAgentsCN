#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runnable scaffold for chapter 6: inspect memory retrieval from a checkpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
CHECKPOINT_ROOT = GENERATIVE_AGENTS / "results" / "checkpoints"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules import utils  # noqa: E402
from modules.memory.associate import Associate  # noqa: E402


ENGLISH_NAMES = {
    "克劳斯": "Klaus Mueller",
    "阿伊莎": "Ayesha Khan",
    "玛丽亚": "Maria Lopez",
    "沃尔夫冈": "Wolfgang Schulz",
    "伊莎贝拉": "Isabella Rodriguez",
}


def display_name(name: str) -> str:
    english = ENGLISH_NAMES.get(name)
    return f"{name} {english}" if english else name


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def checkpoint_file_name(checkpoint_time: str) -> str:
    return f"simulate-{checkpoint_time.replace(':', '')}.json"


def load_associate(experiment: str, checkpoint_time: str, agent: str) -> tuple[Associate, dict]:
    utils.set_timer(checkpoint_time)

    data_config = load_json(GENERATIVE_AGENTS / "data" / "config.json")
    embedding = data_config["agent"]["associate"]["embedding"]

    checkpoint_dir = CHECKPOINT_ROOT / experiment
    state_path = checkpoint_dir / checkpoint_file_name(checkpoint_time)
    state = load_json(state_path)
    memory = state["agents"][agent]["associate"]["memory"]

    associate_path = checkpoint_dir / "storage" / agent / "associate"
    associate = Associate(str(associate_path), embedding, memory=memory)
    return associate, memory


def memory_counts(memory: dict) -> str:
    return "event={event}, thought={thought}, chat={chat}".format(
        event=len(memory.get("event", [])),
        thought=len(memory.get("thought", [])),
        chat=len(memory.get("chat", [])),
    )


def concept_line(concept) -> str:
    return (
        f"{concept.node_id} | {concept.node_type} | P.{concept.poignancy} | "
        f"create={concept.create.strftime('%Y%m%d-%H:%M')} | "
        f"address={':'.join(concept.event.address)}"
    )


def print_concepts(concepts, limit: int) -> None:
    if not concepts:
        print("  （没有检索到记忆节点）")
        return
    for index, concept in enumerate(concepts[:limit], start=1):
        print(f"  [{index}] {concept_line(concept)}")
        print(f"      {concept.describe}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="第 6 章检索 Retrieval 脚手架：从 book-custom-discussion 断点读取真实记忆并执行检索。"
    )
    parser.add_argument("--experiment", default="book-custom-discussion")
    parser.add_argument("--agent", default="克劳斯")
    parser.add_argument("--chat-with", default="阿伊莎")
    parser.add_argument("--early-time", default="20240213-10:40")
    parser.add_argument("--late-time", default="20240213-19:50")
    parser.add_argument("--retrieve-max", type=int, default=3)
    parser.add_argument(
        "--focus",
        action="append",
        default=None,
        help="焦点问题 focus；可重复传入。默认使用克劳斯的当天计划和重要近期事件。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.getenv("MINIMAX_API_KEY"):
        print("缺少环境变量 MINIMAX_API_KEY，无法调用 MiniMax embedding 执行向量检索。")
        return 2

    focus = args.focus or [
        f"{args.agent} 在 2024年02月13日（星期二） 的计划。",
        f"在 {args.agent} 的生活中，重要的近期事件。",
    ]

    print("第 6 章检索 Retrieval 脚本应用")
    print("=" * 64)
    print(f"实验 experiment: {args.experiment}")
    print(f"角色 agent: {display_name(args.agent)}")
    print(f"对话对象 chat_with: {display_name(args.chat_with)}")
    print(f"焦点问题数量 focus_count: {len(focus)}")
    print()

    print(f"[1] 读取早期断点 checkpoint: {args.early_time}")
    early_associate, early_memory = load_associate(args.experiment, args.early_time, args.agent)
    print(f"记忆清单 memory: {memory_counts(early_memory)}")
    print(f"执行 retrieve_chats(\"{args.chat_with}\")")
    early_chats = early_associate.retrieve_chats(args.chat_with)
    print_concepts(early_chats, args.retrieve_max)
    print()

    print(f"[2] 读取后期断点 checkpoint: {args.late_time}")
    late_associate, late_memory = load_associate(args.experiment, args.late_time, args.agent)
    print(f"记忆清单 memory: {memory_counts(late_memory)}")
    print("候选范围 candidate_scope: retrieve_focus 默认检索 event + thought，不直接检索 chat")
    retrieved = late_associate.retrieve_focus(focus, retrieve_max=args.retrieve_max, reduce_all=False)
    for text, concepts in retrieved.items():
        print()
        print(f"焦点问题 focus: {text}")
        print_concepts(concepts, args.retrieve_max)
    print()

    print("[3] 读法")
    print("- retrieve_chats() 用对话对象过滤聊天 chat，适合检查两个人最近聊过什么。")
    print("- retrieve_focus() 用焦点问题 focus 检索事件 event 和想法 thought，适合给计划、反思和关系总结提供证据。")
    print("- 同一个角色的记忆数量会持续增长，检索 Retrieval 只把少量 Top-K 记忆交给后续提示词 prompt。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
