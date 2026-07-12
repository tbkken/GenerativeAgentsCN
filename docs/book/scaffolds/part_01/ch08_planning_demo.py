#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chapter 8 planning demo.

The script inspects how a real checkpoint stores planning state and how
reflection thoughts can become planning retrieval input.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
CHECKPOINT_ROOT = GENERATIVE_AGENTS / "results" / "checkpoints"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules import utils  # noqa: E402
from modules.memory.associate import Associate  # noqa: E402
from modules.memory.schedule import Schedule  # noqa: E402


DISPLAY_NAMES = {
    "克劳斯": "克劳斯 Klaus Mueller",
    "阿伊莎": "阿伊莎 Ayesha Khan",
    "玛丽亚": "玛丽亚 Maria Lopez",
    "沃尔夫冈": "沃尔夫冈 Wolfgang Schulz",
    "伊莎贝拉": "伊莎贝拉 Isabella Rodriguez",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def checkpoint_name(time_text: str) -> str:
    return f"simulate-{time_text.replace(':', '')}.json"


def checkpoint_path(experiment: str, time_text: str) -> Path:
    return CHECKPOINT_ROOT / experiment / checkpoint_name(time_text)


def load_state(experiment: str, time_text: str) -> dict[str, Any]:
    path = checkpoint_path(experiment, time_text)
    if not path.exists():
        raise FileNotFoundError(f"checkpoint not found: {path}")
    return load_json(path)


def agent_state(state: dict[str, Any], agent: str) -> dict[str, Any]:
    try:
        return state["agents"][agent]
    except KeyError as exc:
        raise KeyError(f"agent {agent!r} not found in checkpoint") from exc


def format_minute(minute: int) -> str:
    hour, rest = divmod(int(minute), 60)
    return f"{hour:02d}:{rest:02d}"


def plan_span(plan: dict[str, Any]) -> str:
    start = int(plan["start"])
    end = start + int(plan["duration"])
    return f"{format_minute(start)}-{format_minute(end)}"


def action_end(action: dict[str, Any]) -> str:
    start = utils.to_date(action["start"])
    end = start + dt.timedelta(minutes=int(action.get("duration") or 0))
    return end.strftime("%Y%m%d-%H:%M:%S")


def address_text(address: Any) -> str:
    if isinstance(address, list):
        return " > ".join(str(item) for item in address)
    return str(address)


def event_text(event: dict[str, Any] | None) -> str:
    if not event:
        return "无"
    return "{} {} {} @ {}".format(
        event.get("subject"),
        event.get("predicate"),
        event.get("object"),
        address_text(event.get("address")),
    )


def collect_docstore_nodes(value: Any, result: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if "node_id" in value and "metadata" in value:
            result[value["node_id"]] = value
        data = value.get("__data__")
        if isinstance(data, dict) and "node_id" in data and "metadata" in data:
            result[data["node_id"]] = data
        if isinstance(data, dict) and "id_" in data and "metadata" in data:
            result[data["id_"]] = data
        for item in value.values():
            collect_docstore_nodes(item, result)
    elif isinstance(value, list):
        for item in value:
            collect_docstore_nodes(item, result)


def load_docstore_nodes(experiment: str, agent: str) -> dict[str, dict[str, Any]]:
    path = CHECKPOINT_ROOT / experiment / "storage" / agent / "associate" / "docstore.json"
    if not path.exists():
        raise FileNotFoundError(f"docstore not found: {path}")
    result: dict[str, dict[str, Any]] = {}
    collect_docstore_nodes(load_json(path), result)
    return result


def node_text(node: dict[str, Any]) -> str:
    return str(node.get("text") or node.get("__data__", {}).get("text", ""))


def print_reflection_nodes(experiment: str, agent: str, node_ids: list[str]) -> None:
    docstore = load_docstore_nodes(experiment, agent)
    print("反思节点 reflection_thoughts:")
    for node_id in node_ids:
        node = docstore.get(node_id)
        if not node:
            print(f"  {node_id}: not found")
            continue
        metadata = node.get("metadata", {})
        print(
            "  {node_id} | {node_type} | P{poignancy} | create={create}".format(
                node_id=node_id,
                node_type=metadata.get("node_type"),
                poignancy=metadata.get("poignancy"),
                create=metadata.get("create"),
            )
        )
        print(f"    {node_text(node)}")


def load_schedule(agent_data: dict[str, Any]) -> Schedule:
    schedule_data = agent_data["schedule"]
    return Schedule(
        create=schedule_data.get("create"),
        daily_schedule=copy.deepcopy(schedule_data.get("daily_schedule", [])),
    )


def run_checkpoint(args: argparse.Namespace) -> int:
    utils.set_timer(args.time)
    state = load_state(args.experiment, args.time)
    agent_data = agent_state(state, args.agent)
    schedule = load_schedule(agent_data)
    plan, de_plan = schedule.current_plan()
    action = agent_data.get("action") or {}
    event = action.get("event")
    obj_event = action.get("obj_event")

    print("第 8 章规划 Planning 脚本应用：断点复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"时间 checkpoint_time: {args.time}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"当前状态 currently: {agent_data.get('currently')}")
    print(f"日程数量 schedule_items: {len(schedule.daily_schedule)}")
    print()
    print("当前粗计划 current_plan:")
    print(f"  idx={plan.get('idx')} | {plan_span(plan)} | {plan.get('describe')}")
    print("当前子计划 current_de_plan:")
    print(f"  idx={de_plan.get('idx')} | {plan_span(de_plan)} | {de_plan.get('describe')}")
    print()
    print("当前行动 current_action:")
    print(f"  event: {event_text(event)}")
    print(f"  obj_event: {event_text(obj_event)}")
    print(f"  start: {action.get('start')}")
    print(f"  duration: {action.get('duration')} minutes")
    if action.get("start") is not None:
        print(f"  end: {action_end(action)}")
    print()
    print_reflection_nodes(args.experiment, args.agent, ["node_109", "node_110"])
    print()
    print("读法 reading:")
    print("  - current_plan/current_de_plan 来自 Schedule.current_plan()。")
    print("  - current_action 是断点中已经落盘的行动 Action。")
    print("  - node_109 和 node_110 是第 7 章反思 Reflection 生成的 thought，后续规划可通过检索 Retrieval 找回。")
    return 0


def concept_line(concept: Any) -> str:
    return (
        f"{concept.node_id} | {concept.node_type} | P{concept.poignancy} | "
        f"create={concept.create.strftime('%Y%m%d-%H:%M')} | "
        f"{concept.describe}"
    )


def run_retrieve_input(args: argparse.Namespace) -> int:
    if not os.getenv("MINIMAX_API_KEY"):
        print("缺少环境变量 MINIMAX_API_KEY，无法执行 retrieve-input 模式。", file=sys.stderr)
        return 2

    utils.set_timer(args.time)
    state = load_state(args.experiment, args.time)
    agent_data = agent_state(state, args.agent)
    memory = copy.deepcopy(agent_data["associate"]["memory"])
    data_config = load_json(GENERATIVE_AGENTS / "data" / "config.json")
    embedding = data_config["agent"]["associate"]["embedding"]
    source = CHECKPOINT_ROOT / args.experiment / "storage" / args.agent / "associate"

    focus = [
        f"{args.agent} 在 {utils.get_timer().daily_format_cn()} 的计划。",
        f"在 {args.agent} 的生活中，重要的近期事件。",
    ]

    print("第 8 章规划 Planning 脚本应用：反思 thought 进入规划检索")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"时间 checkpoint_time: {args.time}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"检索上限 retrieve_max: {args.retrieve_max}")
    print("规划焦点 planning_focus:")
    for text in focus:
        print(f"  - {text}")
    print()

    with tempfile.TemporaryDirectory(prefix="ch08_planning_") as temp_dir:
        target = Path(temp_dir) / "associate"
        shutil.copytree(source, target)
        old_cwd = Path.cwd()
        try:
            os.chdir(GENERATIVE_AGENTS)
            associate = Associate(str(target), embedding, memory=memory)
            retrieved = associate.retrieve_focus(
                focus, retrieve_max=args.retrieve_max, reduce_all=False
            )
        finally:
            os.chdir(old_cwd)

    found: set[str] = set()
    for text, concepts in retrieved.items():
        print(f"焦点问题 focus: {text}")
        for concept in concepts:
            found.add(concept.node_id)
            print(f"  {concept_line(concept)}")
        print()

    for node_id in ["node_109", "node_110"]:
        status = "命中 retrieved" if node_id in found else "未进入 Top-K"
        print(f"反思节点 {node_id}: {status}")
    print("读法 reading:")
    print("  - Agent.make_schedule() 使用同样的 planning_focus 检索计划和重要近期事件。")
    print("  - 如果反思 thought 被命中，它就会进入 retrieve_plan / retrieve_thought / retrieve_currently 的输入材料。")
    print("  - 本模式使用临时目录复制 associate，不改写原始 checkpoint。")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chapter 8 planning demo")
    parser.add_argument("--mode", choices=["checkpoint", "retrieve-input"], default="checkpoint")
    parser.add_argument("--experiment", default="book-custom-discussion")
    parser.add_argument("--time", default="20240213-14:00")
    parser.add_argument("--agent", default="克劳斯")
    parser.add_argument("--retrieve-max", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "checkpoint":
        return run_checkpoint(args)
    return run_retrieve_input(args)


if __name__ == "__main__":
    raise SystemExit(main())
