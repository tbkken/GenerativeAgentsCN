#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runnable scaffold for chapter 16: inspect one simulation-loop step."""

from __future__ import annotations

import copy
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
GENERATIVE_AGENTS = ROOT / "generative_agents"
STATIC_ROOT = GENERATIVE_AGENTS / "frontend" / "static"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GENERATIVE_AGENTS))

from modules import utils  # noqa: E402
from modules.agent import Agent  # noqa: E402
from modules.game import Game  # noqa: E402
from modules.maze import Maze  # noqa: E402
from modules.memory.associate import Concept  # noqa: E402


SELECTED_AGENTS = ["克劳斯", "玛丽亚"]
START_TIME = "20240213-09:30"
STRIDE_MINUTES = 10


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def agent_path(name: str) -> Path:
    return STATIC_ROOT / "assets" / "village" / "agents" / name / "agent.json"


def build_config() -> dict:
    data_config = load_json(GENERATIVE_AGENTS / "data" / "config.json")
    assets_root = Path("assets") / "village"
    return {
        "stride": STRIDE_MINUTES,
        "time": {"start": START_TIME},
        "maze": {"path": str(assets_root / "maze.json")},
        "agent_base": data_config["agent"],
        "agents": {
            name: {"config_path": str(assets_root / "agents" / name / "agent.json")}
            for name in SELECTED_AGENTS
        },
    }


def stub_completion(self: Agent, func_hint: str, *args, **kwargs):
    """Replace LLM calls with deterministic values for a reproducible loop demo."""
    if not hasattr(self, "_ch16_completion_calls"):
        self._ch16_completion_calls = []
    self._ch16_completion_calls.append(func_hint)

    daily = {
        "8:00": "起床并完成早晨的例行工作",
        "9:00": "阅读并整理文献综述" if self.name == "克劳斯" else "准备课程笔记",
        "10:00": "继续推进上午的学习任务",
        "11:00": "整理资料",
        "12:00": "吃午饭",
        "13:00": "休息",
        "14:00": "继续学习",
        "15:00": "参加小组讨论",
        "16:00": "完成当天任务",
        "17:00": "回到宿舍",
        "18:00": "吃晚饭",
        "19:00": "放松",
        "20:00": "阅读",
        "21:00": "整理明天计划",
        "22:00": "准备睡觉",
        "23:00": "睡觉",
    }

    if func_hint == "wake_up":
        return 8
    if func_hint == "schedule_init":
        return [
            "早上8点起床",
            "上午在奥克山学院学习",
            "中午吃午饭",
            "下午继续完成学习任务",
            "晚上回宿舍休息",
        ]
    if func_hint == "schedule_daily":
        return daily
    if func_hint == "schedule_decompose":
        plan = args[0]
        if self.name == "克劳斯":
            return [("阅读论文资料", 30), ("起草论文开头段落", 30)]
        return [("整理课堂资料", 30), ("准备课程讨论问题", 30)]
    if func_hint == "determine_sector":
        return "奥克山学院"
    if func_hint == "determine_arena":
        return "图书馆" if self.name == "克劳斯" else "教室"
    if func_hint == "determine_object":
        return "图书馆桌子" if self.name == "克劳斯" else "教室学生座位"
    if func_hint == "describe_object":
        obj, describe = args
        return f"{obj}被用于{describe}"
    if func_hint in {"poignancy_event", "poignancy_chat"}:
        return 1
    if func_hint.startswith("decide_"):
        return False
    return None


def stub_add_concept(self: Agent, e_type, event, create=None, expire=None, filling=None):
    """Avoid vector writes; keep the concept object visible to the current step."""
    return Concept.from_event(f"stub_{self.name}_{len(self.concepts)}", e_type, event, poignancy=1)


def build_agent(name: str, agent_base: dict, maze: Maze, conversation: dict, logger, storage_root: Path) -> Agent:
    seed = load_json(agent_path(name))
    agent_config = utils.update_dict(copy.deepcopy(agent_base), copy.deepcopy(seed))
    agent_config["associate"]["embedding"] = {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434",
    }
    agent_config["storage_root"] = str(storage_root / name)
    return Agent(agent_config, maze, conversation, logger)


def action_text(agent: Agent) -> str:
    event = agent.get_event()
    return str(event)


def main() -> int:
    random.seed(16)
    utils.set_timer(START_TIME)
    config = build_config()
    logger = utils.create_io_logger("error")
    conversation: dict = {}
    storage_root = Path(tempfile.mkdtemp(prefix="ch16_loop_"))

    original_completion = Agent.completion
    original_add_concept = Agent._add_concept
    original_reaction = Agent._reaction

    try:
        Agent.completion = stub_completion
        Agent._add_concept = stub_add_concept
        Agent._reaction = lambda self, agents=None, ignore_words=None: False

        maze = Maze(load_json(STATIC_ROOT / config["maze"]["path"]), logger)
        agents = {
            name: build_agent(name, config["agent_base"], maze, conversation, logger, storage_root)
            for name in SELECTED_AGENTS
        }

        game = Game.__new__(Game)
        game.name = "ch16-loop-scaffold"
        game.static_root = str(STATIC_ROOT)
        game.record_iterval = 30
        game.logger = logger
        game.maze = maze
        game.conversation = conversation
        game.agents = agents

        agent_status = {
            name: {"coord": list(agent.coord), "path": []}
            for name, agent in agents.items()
        }

        timer = utils.get_timer()
        sim_time = timer.get_date("%Y%m%d-%H:%M")

        print("第 16 章仿真循环脚手架 simulation-loop scaffold")
        print("=" * 56)
        print(f"选中角色 selected_agents: {', '.join(SELECTED_AGENTS)}")
        print(f"起始时间 start_time: {START_TIME}")
        print(f"步长分钟 stride_minutes: {STRIDE_MINUTES}")
        print(f"配置角色顺序 config_agents_order: {', '.join(config['agents'].keys())}")
        print()

        print("初始角色状态 agent_status")
        for name, status in agent_status.items():
            tile = maze.tile_at(status["coord"])
            print(f"- {name}: 坐标 coord={status['coord']} 地址 address={tile.get_address(as_list=False)} 路径 path={status['path']}")
        print()

        print(f"仿真步 Simulate Step[1/1, time: {timer.get_date()}]")
        for index, (name, status) in enumerate(agent_status.items(), start=1):
            before = list(status["coord"])
            result = Game.agent_think(game, name, status)
            plan = result["plan"]
            info = result["info"]
            agent = game.get_agent(name)
            if plan.get("path"):
                status["coord"], status["path"] = list(plan["path"][-1]), []
            print(f"[{index}] {name}")
            print(f"    输入状态 status_before: 坐标 coord={before}")
            print(f"    当前地址 current_address: {info['address']}")
            print(f"    提示词调用 completion_calls: {', '.join(agent._ch16_completion_calls)}")
            print(f"    行动结果 action_after: {action_text(agent)}")
            print(f"    返回路径长度 returned_path_len: {len(plan['path'])}")
            print(f"    返回路径终点 returned_path_target: {list(plan['path'][-1]) if plan['path'] else '[]'}")
            print(f"    服务端状态 server_status_after: 坐标 coord={status['coord']} 路径 path={status['path']}")
            print(f"    记录标记 record_flag: {info['record']}")
        print()

        print("本仿真步 step 会写入的结果文件")
        print(f"- simulate-{sim_time.replace(':', '')}.json")
        print("- conversation.json")
        print()

        timer.forward(STRIDE_MINUTES)
        print("时间推进 timer.forward()")
        print(f"- before_forward: {sim_time}")
        print(f"- after_forward: {timer.get_date('%Y%m%d-%H:%M')}")
        return 0
    finally:
        Agent.completion = original_completion
        Agent._add_concept = original_add_concept
        Agent._reaction = original_reaction
        shutil.rmtree(storage_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
