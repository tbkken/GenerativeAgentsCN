#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Chapter 7 reflection demo.

Two modes are provided:

- checkpoint: compare real checkpoint states before and after a recorded reflection.
- live: copy one checkpoint into a temporary directory and call Agent.reflect().
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
STATIC_ROOT = GENERATIVE_AGENTS / "frontend" / "static"
CHECKPOINT_ROOT = GENERATIVE_AGENTS / "results" / "checkpoints"

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules import utils  # noqa: E402
from modules.agent import Agent  # noqa: E402
from modules.maze import Maze  # noqa: E402


DISPLAY_NAMES = {
    "克劳斯": "克劳斯 Klaus Mueller",
    "阿伊莎": "阿伊莎 Ayesha Khan",
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


def agent_state(state: dict[str, Any], agent_name: str) -> dict[str, Any]:
    try:
        return state["agents"][agent_name]
    except KeyError as exc:
        raise KeyError(f"agent {agent_name!r} not found in checkpoint") from exc


def memory_counts(agent_data: dict[str, Any]) -> dict[str, int]:
    memory = agent_data.get("associate", {}).get("memory", {})
    return {
        "event": len(memory.get("event", [])),
        "thought": len(memory.get("thought", [])),
        "chat": len(memory.get("chat", [])),
    }


def thought_ids(agent_data: dict[str, Any]) -> set[str]:
    return set(agent_data.get("associate", {}).get("memory", {}).get("thought", []))


def sort_node_ids(node_ids: list[str] | set[str]) -> list[str]:
    def key(node_id: str) -> tuple[int, str]:
        try:
            return int(node_id.split("_", 1)[1]), node_id
        except Exception:
            return 10**9, node_id

    return sorted(node_ids, key=key)


def select_representative_ids(node_ids: set[str], limit: int) -> list[str]:
    preferred = ["node_94", "node_99", "node_109", "node_110"]
    selected: list[str] = []
    for node_id in preferred:
        if node_id in node_ids and node_id not in selected:
            selected.append(node_id)
    for node_id in sort_node_ids(node_ids):
        if len(selected) >= limit:
            break
        if node_id not in selected:
            selected.append(node_id)
    return selected


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


def load_docstore_nodes(experiment: str, agent_name: str) -> dict[str, dict[str, Any]]:
    path = CHECKPOINT_ROOT / experiment / "storage" / agent_name / "associate" / "docstore.json"
    if not path.exists():
        raise FileNotFoundError(f"docstore not found: {path}")
    result: dict[str, dict[str, Any]] = {}
    collect_docstore_nodes(load_json(path), result)
    return result


def node_text(node: dict[str, Any]) -> str:
    text = node.get("text")
    if text is not None:
        return str(text)
    return str(node.get("__data__", {}).get("text", ""))


def print_node(node_id: str, node: dict[str, Any]) -> None:
    metadata = node.get("metadata", {})
    node_type = metadata.get("node_type", "?")
    poignancy = metadata.get("poignancy", "?")
    create = metadata.get("create", "?")
    address = metadata.get("address", "?")
    print(f"  {node_id} | {node_type} | P{poignancy} | create={create} | address={address}")
    print(f"    {node_text(node)}")


def print_state_summary(label: str, time_text: str, agent_data: dict[str, Any]) -> None:
    counts = memory_counts(agent_data)
    status = agent_data.get("status", {})
    print(f"{label}: {time_text}")
    print(f"  status.poignancy: {status.get('poignancy')}")
    print(f"  memory: event={counts['event']}, thought={counts['thought']}, chat={counts['chat']}")
    print(f"  pending_chats: {len(agent_data.get('chats', []))}")


def run_checkpoint(args: argparse.Namespace) -> int:
    before_state = load_state(args.experiment, args.before_time)
    after_state = load_state(args.experiment, args.after_time)
    before_agent = agent_state(before_state, args.agent)
    after_agent = agent_state(after_state, args.agent)
    before_thoughts = thought_ids(before_agent)
    after_thoughts = thought_ids(after_agent)
    new_thoughts = after_thoughts - before_thoughts
    docstore = load_docstore_nodes(args.experiment, args.agent)
    threshold = before_state.get("agent_base", {}).get("think", {}).get("poignancy_max", "?")

    print("第 7 章反思 Reflection 脚本应用：断点复查")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"触发阈值 threshold: poignancy_max={threshold}")
    print_state_summary("反思前 before", args.before_time, before_agent)
    print_state_summary("反思后 after", args.after_time, after_agent)
    print(f"新增想法 new_thoughts: {len(new_thoughts)}")
    print(f"展示代表节点 representative_nodes: {min(args.show_new, len(new_thoughts))}")
    for node_id in select_representative_ids(new_thoughts, args.show_new):
        node = docstore.get(node_id)
        if node:
            print_node(node_id, node)
    print("读法 reading:")
    print("  - 13:50 时 status.poignancy 还没有达到阈值，反思没有启动。")
    print("  - 14:00 时 thought 清单从 1 条增加到 18 条，说明真实运行中发生过反思。")
    print("  - node_109 来自对话后的计划反思，node_110 来自对话后的长期记忆反思。")
    print("  - 当前基线工程没有把 evidence 字段持久化到 docstore.json。")
    return 0


def static_path(path_text: str) -> Path:
    return STATIC_ROOT / path_text.replace("\\", "/")


def build_agent_config(state: dict[str, Any], agent_name: str, storage_root: Path) -> dict[str, Any]:
    agent_data = agent_state(state, agent_name)
    seed_config = load_json(static_path(agent_data["config_path"]))
    config = utils.update_dict(copy.deepcopy(state["agent_base"]), seed_config)
    config = utils.update_dict(config, copy.deepcopy(agent_data))
    config["storage_root"] = str(storage_root)
    return config


def run_live(args: argparse.Namespace) -> int:
    if not os.environ.get("MINIMAX_API_KEY"):
        print("缺少环境变量 MINIMAX_API_KEY，无法执行实时反思 live mode。", file=sys.stderr)
        return 2

    before_state = load_state(args.experiment, args.before_time)
    source_storage = CHECKPOINT_ROOT / args.experiment / "storage" / args.agent / "associate"
    if not source_storage.exists():
        raise FileNotFoundError(f"associate storage not found: {source_storage}")

    calls: list[str] = []
    original_completion = Agent.completion

    def recording_completion(self: Agent, func_hint: str, *completion_args: Any, **kwargs: Any) -> Any:
        calls.append(func_hint)
        return original_completion(self, func_hint, *completion_args, **kwargs)

    print("第 7 章反思 Reflection 脚本应用：实时反思")
    print("=" * 72)
    print(f"实验 experiment: {args.experiment}")
    print(f"角色 agent: {DISPLAY_NAMES.get(args.agent, args.agent)}")
    print(f"临时复制来源 source: {source_storage}")

    with tempfile.TemporaryDirectory(prefix="ch07_reflect_") as temp_dir:
        temp_agent_root = Path(temp_dir) / args.agent
        temp_associate = temp_agent_root / "associate"
        shutil.copytree(source_storage, temp_associate)

        logger = utils.create_io_logger("error")
        utils.set_timer(args.after_time)
        maze = Maze(load_json(static_path(before_state["maze"]["path"])), logger)
        agent_config = build_agent_config(before_state, args.agent, temp_agent_root)
        agent = Agent(agent_config, maze, {}, logger)
        agent.reset()

        before_thoughts = set(agent.associate.memory.get("thought", []))
        original_poignancy = agent.status.get("poignancy")
        agent.status["poignancy"] = max(args.force_poignancy, int(original_poignancy or 0))
        print(f"原始触动程度 original_poignancy: {original_poignancy}")
        print(f"强制触动程度 forced_poignancy: {agent.status['poignancy']}")
        print(f"反思前 thought_count_before: {len(before_thoughts)}")

        old_cwd = Path.cwd()
        Agent.completion = recording_completion
        try:
            os.chdir(GENERATIVE_AGENTS)
            agent.reflect()
        finally:
            os.chdir(old_cwd)
            Agent.completion = original_completion

        after_thoughts = set(agent.associate.memory.get("thought", []))
        new_thoughts = after_thoughts - before_thoughts
        print("调用链 completion_calls:")
        for name, count in Counter(calls).items():
            print(f"  {name}: {count}")
        print(f"反思后 thought_count_after: {len(after_thoughts)}")
        print(f"新增想法 new_thoughts: {len(new_thoughts)}")
        print(f"展示代表节点 representative_nodes: {min(args.show_new, len(new_thoughts))}")
        for node_id in select_representative_ids(new_thoughts, args.show_new):
            concept = agent.associate.find_concept(node_id)
            print(f"  {node_id} | {concept.node_type} | P{concept.poignancy} | create={concept.create}")
            print(f"    {concept.describe}")
        print("读法 reading:")
        print("  - live 模式使用临时目录，不改写原始 checkpoint。")
        print("  - 输出中的 prompt 名称来自真实 Agent.reflect() 调用链。")
        print("  - 新增 thought 内容由现场 LLM 调用生成，可能与历史 checkpoint 不完全一致。")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chapter 7 reflection demo")
    parser.add_argument("--mode", choices=["checkpoint", "live"], default="checkpoint")
    parser.add_argument("--experiment", default="book-custom-discussion")
    parser.add_argument("--agent", default="克劳斯")
    parser.add_argument("--before-time", default="20240213-13:50")
    parser.add_argument("--after-time", default="20240213-14:00")
    parser.add_argument("--force-poignancy", type=int, default=153)
    parser.add_argument("--show-new", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "checkpoint":
        return run_checkpoint(args)
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
