#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chapter 9 reacting demo.

The script inspects a real checkpoint where a field event becomes a chat action.
It does not call an LLM or modify checkpoints.
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
    "伊莎贝拉": "伊莎贝拉 Isabella Rodriguez",
    "沃尔夫冈": "沃尔夫冈 Wolfgang Schulz",
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


def conversation_key_time(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d-%H:%M")


def find_conversation(
    experiment: str, value: dt.datetime, agent: str, other: str
) -> tuple[str, list[list[str]]]:
    path = CHECKPOINT_ROOT / experiment / "conversation.json"
    if not path.exists():
        return "", []
    data = load_json(path)
    if not isinstance(data, dict):
        return "", []
    key_time = conversation_key_time(value)
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


def has_address(agent_data: dict[str, Any]) -> bool:
    return bool(event_of(agent_data).get("address"))


def is_sleeping(agent_data: dict[str, Any]) -> bool:
    text = str(event_of(agent_data).get("describe") or event_of(agent_data).get("object") or "")
    return "睡觉" in text or "sleeping" in text


def is_pending(agent_data: dict[str, Any]) -> bool:
    return event_of(agent_data).get("predicate") == "待开始"


def run_checkpoint(args: argparse.Namespace) -> int:
    current_time = parse_time(args.time)
    prev_time = current_time - dt.timedelta(minutes=args.stride)
    next_time = current_time + dt.timedelta(minutes=args.stride)

    prev_state = load_state(args.experiment, prev_time)
    current_state = load_state(args.experiment, current_time)
    next_state = load_state(args.experiment, next_time)

    prev_agent = agent_state(prev_state, args.agent)
    current_agent = agent_state(current_state, args.agent)
    next_agent = agent_state(next_state, args.agent)
    conv_key, chats = find_conversation(args.experiment, current_time, args.agent, args.other)

    print("第 9 章反应 Reacting 脚本应用：断点复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"对象 other: {DISPLAY_NAMES.get(args.other, args.other)}")
    print(f"反应时间 reaction_time: {args.time}")
    print()
    describe_action(f"反应前 before_action @ {time_text(prev_time)}", prev_agent)
    print()
    describe_action(f"触发事件 trigger_event @ {time_text(prev_time)}", agent_state(prev_state, args.other))
    print()
    describe_action(f"反应中 reaction_action @ {time_text(current_time)}", current_agent)
    print()
    print(f"conversation_key: {conv_key or '未找到'}")
    print_conversation(chats)
    print()
    describe_action(f"反应后 after_action @ {time_text(next_time)}", next_agent)
    print()
    print("读法 reading:")
    print("  - 反应前，克劳斯仍在执行论文写作计划。")
    print("  - 触发事件来自阿伊莎的现场行动，成为反应 Reacting 的焦点 focus。")
    print("  - 反应中，当前 Action 被改写成 predicate=对话、object=阿伊莎。")
    print("  - 反应后，克劳斯回到论文写作行动，说明反应不是永久替换整天计划。")
    return 0


def run_gates(args: argparse.Namespace) -> int:
    current_time = parse_time(args.time)
    prev_time = current_time - dt.timedelta(minutes=args.stride)
    prev_state = load_state(args.experiment, prev_time)
    current_state = load_state(args.experiment, current_time)
    prev_agent = agent_state(prev_state, args.agent)
    prev_other = agent_state(prev_state, args.other)
    current_agent = agent_state(current_state, args.agent)
    conv_key, chats = find_conversation(args.experiment, current_time, args.agent, args.other)
    current_event = event_of(current_agent)
    hour = current_time.hour

    focus_is_agent = bool(conv_key and args.other in current_event.get("object", ""))
    skip_react = (
        hour >= 23
        or not has_address(prev_agent)
        or not has_address(prev_other)
        or is_sleeping(prev_agent)
        or is_sleeping(prev_other)
        or is_pending(prev_agent)
        or is_pending(prev_other)
    )
    chat_hit = (
        current_event.get("predicate") == "对话"
        and current_event.get("object") == args.other
        and bool(chats)
    )
    wait_used = current_event.get("predicate") == "waiting to start"

    print("第 9 章反应 Reacting 脚本应用：门禁复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"反应时间 reaction_time: {args.time}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"对象 other: {DISPLAY_NAMES.get(args.other, args.other)}")
    print()
    print("门禁 gates:")
    print(f"  focus_is_agent: {'是' if focus_is_agent else '否'}")
    print(f"    evidence: conversation_key={conv_key or '未找到'}")
    print(f"  skip_react: {'是' if skip_react else '否'}")
    print(
        "    evidence: hour={}, self_has_address={}, other_has_address={}, "
        "self_sleeping={}, other_sleeping={}, self_pending={}, other_pending={}".format(
            hour,
            has_address(prev_agent),
            has_address(prev_other),
            is_sleeping(prev_agent),
            is_sleeping(prev_other),
            is_pending(prev_agent),
            is_pending(prev_other),
        )
    )
    print(f"  chat_branch: {'命中 hit' if chat_hit else '未命中'}")
    print(
        "    evidence: action.predicate={}, action.object={}, chats={}".format(
            current_event.get("predicate"),
            current_event.get("object"),
            len(chats),
        )
    )
    print(f"  wait_branch: {'命中 hit' if wait_used else '未作为本次主结果'}")
    print("    evidence: 本次 checkpoint 的最终 Action 是对话，不是 waiting to start。")
    print()
    print("最终结果 final_action:")
    print(f"  {args.agent} {current_event.get('predicate')} {current_event.get('object')}")
    print(f"  describe: {current_event.get('describe')}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chapter 9 reacting demo")
    parser.add_argument("--mode", choices=["checkpoint", "gates"], default="checkpoint")
    parser.add_argument("--experiment", default="book-custom-discussion")
    parser.add_argument("--time", default="20240213-10:20")
    parser.add_argument("--agent", default="克劳斯")
    parser.add_argument("--other", default="阿伊莎")
    parser.add_argument("--stride", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "checkpoint":
        return run_checkpoint(args)
    return run_gates(args)


if __name__ == "__main__":
    raise SystemExit(main())
