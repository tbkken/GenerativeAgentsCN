#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chapter 10 dialogue demo.

The script inspects one real dialogue in book-custom-discussion. It does not
call an LLM and does not modify checkpoints.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
CHECKPOINT_ROOT = ROOT / "generative_agents" / "results" / "checkpoints"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DISPLAY_NAMES = {
    "克劳斯": "克劳斯 Klaus Mueller",
    "阿伊莎": "阿伊莎 Ayesha Khan",
    "玛丽亚": "玛丽亚 Maria Lopez",
    "沃尔夫冈": "沃尔夫冈 Wolfgang Schulz",
    "伊莎贝拉": "伊莎贝拉 Isabella Rodriguez",
}


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y%m%d-%H:%M")


def time_text(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d-%H:%M")


def checkpoint_file(value: dt.datetime) -> str:
    return f"simulate-{value.strftime('%Y%m%d-%H%M')}.json"


def checkpoint_path(experiment: str, value: dt.datetime) -> Path:
    return CHECKPOINT_ROOT / experiment / checkpoint_file(value)


def load_state(experiment: str, value: dt.datetime) -> dict[str, Any]:
    path = checkpoint_path(experiment, value)
    if not path.exists():
        raise FileNotFoundError(f"checkpoint not found: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"checkpoint is not a JSON object: {path}")
    return data


def agent_state(state: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return state["agents"][name]
    except KeyError as exc:
        raise KeyError(f"agent {name!r} not found in checkpoint") from exc


def event_of(agent_data: dict[str, Any]) -> dict[str, Any]:
    return (agent_data.get("action") or {}).get("event") or {}


def address_text(address: Any) -> str:
    if isinstance(address, list):
        return " > ".join(str(item) for item in address)
    return str(address or "无")


def describe_action(label: str, agent_data: dict[str, Any]) -> None:
    action = agent_data.get("action") or {}
    event = action.get("event") or {}
    print(f"{label}:")
    print(f"  predicate: {event.get('predicate')}")
    print(f"  object: {event.get('object')}")
    print(f"  describe: {event.get('describe')}")
    print(f"  address: {address_text(event.get('address'))}")
    print(f"  start: {action.get('start')}")
    print(f"  duration: {action.get('duration')} minutes")


def conversation_path(experiment: str) -> Path:
    return CHECKPOINT_ROOT / experiment / "conversation.json"


def find_conversation(
    experiment: str, value: dt.datetime, agent: str, other: str
) -> tuple[str, list[list[str]]]:
    path = conversation_path(experiment)
    if not path.exists():
        return "", []
    data = load_json(path)
    if not isinstance(data, dict):
        return "", []
    key_time = time_text(value)
    for item in data.get(key_time, []):
        if not isinstance(item, dict):
            continue
        for key, chats in item.items():
            if f"{agent} -> {other}" in key and isinstance(chats, list):
                return key, chats
    return "", []


def print_conversation(chats: list[list[str]]) -> None:
    print("conversation:")
    for idx, item in enumerate(chats, start=1):
        if len(item) < 2:
            continue
        print(f"  {idx}. {item[0]}: {item[1]}")


def node_data(raw_node: dict[str, Any]) -> dict[str, Any]:
    data = raw_node.get("__data__")
    if isinstance(data, dict):
        return data
    return raw_node


def load_docstore_nodes(experiment: str, agent: str) -> dict[str, dict[str, Any]]:
    path = CHECKPOINT_ROOT / experiment / "storage" / agent / "associate" / "docstore.json"
    if not path.exists():
        raise FileNotFoundError(f"docstore not found: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"docstore is not a JSON object: {path}")
    nodes = data.get("docstore/data", {})
    if not isinstance(nodes, dict):
        return {}
    return {node_id: node_data(node) for node_id, node in nodes.items()}


def find_chat_node(
    experiment: str,
    owner: str,
    subject: str,
    object_name: str,
    summary: str,
) -> tuple[str, dict[str, Any] | None]:
    for node_id, node in load_docstore_nodes(experiment, owner).items():
        metadata = node.get("metadata") or {}
        if metadata.get("node_type") != "chat":
            continue
        if metadata.get("subject") != subject or metadata.get("object") != object_name:
            continue
        if str(node.get("text") or "") == summary:
            return node_id, node
    return "", None


def print_chat_node(label: str, node_id: str, node: dict[str, Any] | None) -> None:
    print(f"{label}:")
    if not node:
        print("  not found")
        return
    metadata = node.get("metadata") or {}
    print(f"  node_id: {node_id}")
    print(f"  node_type: {metadata.get('node_type')}")
    print(f"  subject: {metadata.get('subject')}")
    print(f"  object: {metadata.get('object')}")
    print(f"  poignancy: {metadata.get('poignancy')}")
    print(f"  create: {metadata.get('create')}")
    print(f"  text: {node.get('text')}")


def current_chats(agent_data: dict[str, Any]) -> list[Any]:
    chats = agent_data.get("chats")
    return chats if isinstance(chats, list) else []


def summary_from_action(agent_data: dict[str, Any]) -> str:
    return str(event_of(agent_data).get("describe") or "")


def run_checkpoint(args: argparse.Namespace) -> int:
    value = parse_time(args.time)
    next_value = value + dt.timedelta(minutes=args.stride)
    state = load_state(args.experiment, value)
    next_state = load_state(args.experiment, next_value)
    agent_data = agent_state(state, args.agent)
    other_data = agent_state(state, args.other)
    next_agent_data = agent_state(next_state, args.agent)
    conv_key, chats = find_conversation(args.experiment, value, args.agent, args.other)
    summary = summary_from_action(agent_data)
    agent_node_id, agent_node = find_chat_node(
        args.experiment, args.agent, args.agent, args.other, summary
    )
    other_node_id, other_node = find_chat_node(
        args.experiment, args.other, args.other, args.agent, summary
    )

    print("第 10 章对话 Dialogue 脚本应用：断点复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"对象 other: {DISPLAY_NAMES.get(args.other, args.other)}")
    print(f"对话时间 dialogue_time: {args.time}")
    print()
    describe_action(f"对话行动 dialogue_action @ {time_text(value)}", agent_data)
    print()
    describe_action(f"对方当前行动 other_action @ {time_text(value)}", other_data)
    print()
    print(f"conversation_key: {conv_key or '未找到'}")
    print_conversation(chats)
    print()
    print_chat_node(f"{args.agent} 的 chat 记忆节点", agent_node_id, agent_node)
    print()
    print_chat_node(f"{args.other} 的 chat 记忆节点", other_node_id, other_node)
    print()
    describe_action(f"对话后 after_action @ {time_text(next_value)}", next_agent_data)
    print()
    print("读法 reading:")
    print("  - Action 保存这次对话的摘要、对象、地点和持续时间。")
    print("  - conversation.json 保存逐句原文，适合回放和复查。")
    print("  - 双方 docstore 都出现 chat 节点，但 subject/object 会按各自视角反转。")
    print("  - 下一断点显示克劳斯回到论文写作，对话没有永久吞掉原计划。")
    return 0


def run_writeback(args: argparse.Namespace) -> int:
    value = parse_time(args.time)
    state = load_state(args.experiment, value)
    agent_data = agent_state(state, args.agent)
    other_data = agent_state(state, args.other)
    conv_key, chats = find_conversation(args.experiment, value, args.agent, args.other)
    summary = summary_from_action(agent_data)
    agent_node_id, agent_node = find_chat_node(
        args.experiment, args.agent, args.agent, args.other, summary
    )
    other_node_id, other_node = find_chat_node(
        args.experiment, args.other, args.other, args.agent, summary
    )
    agent_chats = current_chats(agent_data)
    other_chats = current_chats(other_data)

    print("第 10 章对话 Dialogue 脚本应用：写回复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"对话 dialogue: {DISPLAY_NAMES.get(args.agent, args.agent)} -> {DISPLAY_NAMES.get(args.other, args.other)}")
    print(f"时间 time: {args.time}")
    print()
    print("写回位置 writeback:")
    print(f"  conversation.json: {'命中' if conv_key and chats else '未命中'} | key={conv_key or '无'} | turns={len(chats)}")
    print(f"  Action: {'命中' if event_of(agent_data).get('predicate') == '对话' else '未命中'} | predicate={event_of(agent_data).get('predicate')} | object={event_of(agent_data).get('object')}")
    print(f"  {args.agent} docstore chat: {'命中' if agent_node else '未命中'} | node_id={agent_node_id or '无'}")
    print(f"  {args.other} docstore chat: {'命中' if other_node else '未命中'} | node_id={other_node_id or '无'}")
    print(f"  {args.agent} checkpoint chats: {'命中' if agent_chats else '未命中'} | turns={len(agent_chats)}")
    print(f"  {args.other} checkpoint chats: {'命中' if other_chats else '未命中'} | turns={len(other_chats)}")
    print()
    print("对话摘要 summary:")
    print(f"  {summary}")
    print()
    print("反思输入 reflection_input:")
    print("  checkpoint 中的 chats 会被 Agent.reflect() 读取，用于生成对话后的 thought；本脚本只复查输入是否已经写入，不重跑反思。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one real dialogue writeback.")
    parser.add_argument(
        "--mode",
        choices=("checkpoint", "writeback"),
        default="checkpoint",
        help="checkpoint shows the dialogue scene; writeback verifies persisted outputs.",
    )
    parser.add_argument("--experiment", default="book-custom-discussion")
    parser.add_argument("--time", default="20240213-10:20")
    parser.add_argument("--agent", default="克劳斯")
    parser.add_argument("--other", default="阿伊莎")
    parser.add_argument("--stride", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == "checkpoint":
        return run_checkpoint(args)
    return run_writeback(args)


if __name__ == "__main__":
    raise SystemExit(main())
