#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build reusable evidence traces for chapters 17-23.

The script avoids LLM/API calls. It reads local project materials, refreshes the
chapter mechanism figures, writes chapter-level JSON traces, and prints a small
stdout summary that the manuscript can cite directly.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
GA = ROOT / "generative_agents"
ASSET_ROOT = ROOT / "docs" / "book" / "assets"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(GA))

from modules.maze import Maze  # noqa: E402
from modules.model.llm_model import parse_structured_output  # noqa: E402
from pydantic import BaseModel  # noqa: E402


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_trace(chapter: int, file_name: str, payload: dict) -> Path:
    out = ASSET_ROOT / f"chapter_{chapter}" / file_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def refresh_figures() -> list[Path]:
    figure_script = Path(__file__).with_name("ch17_23_mechanism_figures.py")
    spec = importlib.util.spec_from_file_location("ch17_23_mechanism_figures", figure_script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load figure script: {figure_script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [
        module.chapter17(),
        module.chapter18(),
        module.chapter19(),
        module.chapter20(),
        module.chapter21(),
        module.chapter22(),
        module.chapter23(),
    ]


def first_active_replay_coord() -> tuple[list[int], str]:
    movement_path = GA / "results" / "compressed" / "book-config-ai-seminar" / "movement.json"
    if not movement_path.exists():
        return [119, 24], "fallback: chapter 13 text"
    movement = load_json(movement_path)
    frames = movement.get("all_movement", {})
    for key in sorted((k for k in frames if k.isdigit()), key=int):
        frame = frames[key]
        for data in frame.values():
            if "movement" in data:
                return data["movement"], rel(movement_path)
    return [119, 24], rel(movement_path)


def trace_chapter17() -> dict:
    coord, coord_source = first_active_replay_coord()
    config = load_json(GA / "data" / "config.json")["agent"]["percept"]
    maze = Maze(load_json(GA / "frontend" / "static" / "assets" / "village" / "maze.json"), None)
    scope = maze.get_scope(coord, config)
    arena = maze.tile_at(coord).get_address("arena")
    same_arena_tiles = [tile for tile in scope if tile.get_address("arena") == arena]

    events = {}
    for tile in same_arena_tiles:
        for event in tile.get_events():
            events[event.get_describe()] = ":".join(event.address)

    return {
        "source": {
            "coord": coord_source,
            "config": rel(GA / "data" / "config.json"),
            "maze": rel(GA / "frontend" / "static" / "assets" / "village" / "maze.json"),
            "code": rel(GA / "modules" / "agent.py"),
        },
        "coord": coord,
        "current_arena": arena,
        "percept_config": config,
        "scope_count": len(scope),
        "same_arena_tiles": len(same_arena_tiles),
        "game_object_tiles_in_scope": sum(1 for tile in scope if tile.has_address("game_object")),
        "event_descriptions_in_same_arena": sorted(events.keys())[: config["att_bandwidth"]],
        "agent_percept_order": [
            "Maze.get_scope(coord, percept_config)",
            "tile.get_address('arena') == current_arena",
            "tile.get_events()",
            "distance sort",
            "att_bandwidth slice",
            "recent memory de-dup",
            "Agent._add_concept()",
        ],
    }


def trace_chapter18() -> dict:
    config = load_json(GA / "data" / "config.json")["agent"]["associate"]
    source = read_text(GA / "modules" / "memory" / "associate.py")
    metadata_keys = re.findall(r'"([^"]+)":', source[source.find("metadata = {"): source.find("node = self._index.add_node")])
    methods = re.findall(r"def (retrieve_\w+|add_node|cleanup_index|to_dict)\(", source)
    return {
        "source": {
            "associate": rel(GA / "modules" / "memory" / "associate.py"),
            "index": rel(GA / "modules" / "storage" / "index.py"),
            "config": rel(GA / "data" / "config.json"),
        },
        "memory_types": ["event", "thought", "chat"],
        "associate_config": config,
        "add_node_metadata_keys": metadata_keys,
        "public_methods": methods,
        "retrieval_formula": "final_score = recency + relevance + importance",
        "evidence_boundary": "Agent.reflect() passes filling/evidence into Agent._add_concept(), but Associate.add_node() does not persist filling in metadata.",
    }


def prompt_sizes(names: list[str]) -> dict[str, int]:
    return {
        name: (GA / "data" / "prompts" / f"{name}.txt").stat().st_size
        for name in names
        if (GA / "data" / "prompts" / f"{name}.txt").exists()
    }


def trace_chapter19() -> dict:
    prompts = ["wake_up", "schedule_init", "schedule_daily", "schedule_decompose", "schedule_revise"]
    return {
        "source": {
            "schedule": rel(GA / "modules" / "memory" / "schedule.py"),
            "agent": rel(GA / "modules" / "agent.py"),
            "scratch": rel(GA / "modules" / "prompt" / "scratch.py"),
            "prompts": rel(GA / "data" / "prompts"),
        },
        "prompt_template_bytes": prompt_sizes(prompts),
        "pipeline": [
            "prompt_wake_up() -> wake_up hour",
            "prompt_schedule_init() -> coarse activity list",
            "prompt_schedule_daily() -> 24-hour dict",
            "Schedule.add_plan() -> daily_schedule item",
            "Schedule.decompose() + prompt_schedule_decompose() -> minute tasks",
            "Agent._determine_action() -> address and Action",
            "prompt_schedule_revise() -> interrupted plan repair",
        ],
        "sample_daily_schedule_item": {
            "idx": 0,
            "describe": "阅读并整理文献综述",
            "start": 540,
            "duration": 60,
            "decompose": [
                {"idx": 0, "describe": "阅读论文资料", "start": 540, "duration": 30},
                {"idx": 1, "describe": "起草论文开头段落", "start": 570, "duration": 30},
            ],
        },
        "source_boundary": "Schedule.decompose() contains mixed English/Chinese sleep checks; treat it as current code behavior, not a universal sleep design.",
    }


def trace_chapter20() -> dict:
    prompts = [
        "decide_chat",
        "summarize_relation",
        "generate_chat",
        "generate_chat_check_repeat",
        "decide_chat_terminate",
        "summarize_chats",
        "decide_wait",
        "reflect_chat_planing",
        "reflect_chat_memory",
    ]
    conversation_path = GA / "results" / "checkpoints" / "book-config-ai-seminar" / "conversation.json"
    conversation = load_json(conversation_path) if conversation_path.exists() else {}
    return {
        "source": {
            "agent": rel(GA / "modules" / "agent.py"),
            "prompts": rel(GA / "data" / "prompts"),
            "conversation": rel(conversation_path) if conversation_path.exists() else None,
        },
        "prompt_template_bytes": prompt_sizes(prompts),
        "pipeline": [
            "Agent._reaction() selects focus from self.concepts",
            "Agent._chat_with() checks schedules, cooldown and decide_chat",
            "generate_chat loop creates both sides of dialogue",
            "summarize_chats compresses dialogue",
            "schedule_chat() revises both agents' current action",
            "reflect_chat_planing / reflect_chat_memory later convert chats into thoughts",
        ],
        "conversation_keys": sorted(conversation.keys()),
        "conversation_entry_count": sum(len(v) for v in conversation.values()),
        "wait_path": "Agent._wait_other() only runs when the agent already has a path and the target address is occupied by the other agent.",
    }


def trace_chapter21() -> dict:
    source = read_text(GA / "modules" / "memory" / "associate.py")
    return {
        "source": {
            "agent": rel(GA / "modules" / "agent.py"),
            "associate": rel(GA / "modules" / "memory" / "associate.py"),
            "config": rel(GA / "data" / "config.json"),
            "prompts": rel(GA / "data" / "prompts"),
        },
        "poignancy_max": load_json(GA / "data" / "config.json")["agent"]["think"]["poignancy_max"],
        "prompt_template_bytes": prompt_sizes(["reflect_focus", "reflect_insights", "reflect_chat_planing", "reflect_chat_memory"]),
        "pipeline": [
            "status['poignancy'] threshold check",
            "retrieve_events() + retrieve_thoughts()",
            "prompt_reflect_focus()",
            "retrieve_focus(focus, reduce_all=False)",
            "prompt_reflect_insights()",
            "Agent._add_concept('thought', filling=evidence)",
            "status['poignancy'] = 0 and chats = []",
        ],
        "evidence_persisted_in_metadata": '"filling"' in source[source.find("metadata = {"): source.find("node = self._index.add_node")],
        "metadata_boundary": "evidence reaches Agent._add_concept(..., filling=evidence), but current Associate.add_node() metadata does not include filling.",
    }


class IntResponse(BaseModel):
    res: int


class BoolResponse(BaseModel):
    res: bool


def trace_chapter22() -> dict:
    config = load_json(GA / "data" / "config.json")["agent"]
    raw_with_think = '<think>draft reasoning</think>{"res": 7}'
    cleaned = re.sub(r"<think>.*?</think>", "", raw_with_think, flags=re.DOTALL).strip()
    cases = {
        "clean_json_int": parse_structured_output('{"res": 7}', IntResponse, "ch22"),
        "wrapped_json_bool": parse_structured_output("prefix {\"res\": false} suffix", BoolResponse, "ch22"),
        "think_filtered_int": parse_structured_output(cleaned, IntResponse, "ch22"),
        "falsey_response_boundary": 0 or 8,
    }
    return {
        "source": {
            "llm_model": rel(GA / "modules" / "model" / "llm_model.py"),
            "scratch": rel(GA / "modules" / "prompt" / "scratch.py"),
            "index": rel(GA / "modules" / "storage" / "index.py"),
            "config": rel(GA / "data" / "config.json"),
        },
        "think_provider": config["think"]["llm"],
        "embedding_provider": config["associate"]["embedding"],
        "structured_output_cases": cases,
        "providers": {
            "llm": ["ollama", "minimax", "openai"],
            "embedding": ["hugging_face", "ollama", "minimax", "openai"],
        },
        "boundary": "LLMModel.completion() returns response or failsafe, so falsey values such as 0 can be replaced by failsafe.",
    }


def trace_chapter23() -> dict:
    sim_name = "book-party-pair"
    checkpoint_root = GA / "results" / "checkpoints" / sim_name
    compressed_root = GA / "results" / "compressed" / sim_name
    movement = load_json(compressed_root / "movement.json")
    simulation = read_text(compressed_root / "simulation.md")
    checkpoints = sorted(checkpoint_root.glob("simulate-*.json"))
    frames = movement["all_movement"]
    return {
        "source": {
            "compress": rel(GA / "compress.py"),
            "replay": rel(GA / "replay.py"),
            "checkpoints": rel(checkpoint_root),
            "compressed": rel(compressed_root),
        },
        "simulation_name": sim_name,
        "checkpoint_count": len(checkpoints),
        "frames_per_step": 60,
        "movement_frame_count": len([k for k in frames if k.isdigit()]),
        "non_empty_frame_count": len([v for k, v in frames.items() if k.isdigit() and v]),
        "agent_names": list(movement["persona_init_pos"].keys()),
        "conversation_key_count": len(movement["all_movement"].get("conversation", {})),
        "simulation_md_chars": len(simulation),
        "generated_files": [rel(compressed_root / "movement.json"), rel(compressed_root / "simulation.md")],
    }


def main() -> int:
    print("第 17-23 章机制证据脚手架 part03 evidence")
    traces = {
        17: ("ch17_perception_trace.json", trace_chapter17()),
        18: ("ch18_memory_trace.json", trace_chapter18()),
        19: ("ch19_schedule_trace.json", trace_chapter19()),
        20: ("ch20_social_trace.json", trace_chapter20()),
        21: ("ch21_reflection_trace.json", trace_chapter21()),
        22: ("ch22_model_adapter_trace.json", trace_chapter22()),
        23: ("ch23_replay_trace.json", trace_chapter23()),
    }

    trace_paths = []
    for chapter, (file_name, payload) in traces.items():
        trace_paths.append(write_trace(chapter, file_name, payload))

    figure_paths = refresh_figures()

    ch17 = traces[17][1]
    print(
        "chapter17 percept: "
        f"coord={tuple(ch17['coord'])}, scope_count={ch17['scope_count']}, "
        f"same_arena_tiles={ch17['same_arena_tiles']}, "
        f"events_in_same_arena={len(ch17['event_descriptions_in_same_arena'])}"
    )
    ch18 = traces[18][1]
    print(
        "chapter18 memory: "
        f"memory_types={','.join(ch18['memory_types'])}, "
        f"metadata_keys={','.join(ch18['add_node_metadata_keys'])}, "
        f"retrieval={ch18['retrieval_formula']}"
    )
    ch19 = traces[19][1]
    print(
        "chapter19 schedule: "
        f"prompt_count={len(ch19['prompt_template_bytes'])}, "
        f"sample_plan={ch19['sample_daily_schedule_item']['describe']}, "
        "decompose_items=2"
    )
    ch20 = traces[20][1]
    print(
        "chapter20 social: "
        f"prompt_count={len(ch20['prompt_template_bytes'])}, "
        f"conversation_keys={len(ch20['conversation_keys'])}, "
        f"conversation_entries={ch20['conversation_entry_count']}"
    )
    ch21 = traces[21][1]
    print(
        "chapter21 reflection: "
        f"poignancy_max={ch21['poignancy_max']}, "
        f"reflect_prompt_count={len(ch21['prompt_template_bytes'])}, "
        f"evidence_persisted_in_metadata={ch21['evidence_persisted_in_metadata']}"
    )
    ch22 = traces[22][1]
    print(
        "chapter22 model: "
        f"think_provider={ch22['think_provider']['provider']}, "
        f"embedding_provider={ch22['embedding_provider']['provider']}, "
        f"falsey_response_boundary={ch22['structured_output_cases']['falsey_response_boundary']}"
    )
    ch23 = traces[23][1]
    print(
        "chapter23 replay: "
        f"checkpoints={ch23['checkpoint_count']}, "
        f"movement_frames={ch23['movement_frame_count']}, "
        f"agents={','.join(ch23['agent_names'])}, "
        f"simulation_md_chars={ch23['simulation_md_chars']}"
    )

    for path in figure_paths:
        print(f"figure: {rel(path)}")
    for path in trace_paths:
        print(f"trace: {rel(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
