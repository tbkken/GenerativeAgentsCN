#!/usr/bin/env python
"""Chapter 29 LLM-as-Judge evaluator.

The deterministic evaluator extracts evidence and simple metrics from a
simulation result. This script reads that metrics JSON, builds a compact
evidence bundle, and asks an LLM judge to score believable behavior with a
fixed rubric and structured JSON output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

import requests


DIMENSION_RUBRICS = {
    "planning_quality": {
        "label": "计划合理性 planning quality",
        "question": "角色日程、当前计划和实际行动是否互相支持。",
        "strong": "日程无明显冲突，行动地点合理，行动能解释当前计划和当前目标。",
        "weak": "日程冲突、行动泛化、地点错误，或当前行动与计划目标脱节。",
    },
    "social_diffusion": {
        "label": "社会传播可信度 social diffusion credibility",
        "question": "信息是否有源头、有路径，并在传播中保留核心事实。",
        "strong": "传播源头清晰，传播边可追踪，核心事实保持，后续对话或行动受到影响。",
        "weak": "多人突然知道事件，传播路径缺失，核心事实变形，或只是在关键词层面重复。",
    },
    "action_grounding": {
        "label": "行动落地能力 action grounding",
        "question": "口头承诺、地点移动和行动描述是否形成闭环。",
        "strong": "承诺在容差窗口内到达目标地点，到场后行动与承诺主题一致。",
        "weak": "承诺不到场、到场但行动无关，或行动没有任何前置动机。",
    },
    "memory_continuity": {
        "label": "记忆连续性 memory continuity",
        "question": "关键事件是否进入记忆，并能在后续对话或行动中被使用。",
        "strong": "事件、对话和想法节点完整，后续发言能正确使用这些经历。",
        "weak": "关键经历没有写入，后续无法召回，或凭空编造不存在的经历。",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge one chapter 29 metrics JSON with an LLM.")
    parser.add_argument("--metrics-json", required=True, help="Path produced by ch29_evaluate_simulation.py.")
    parser.add_argument("--output", required=True, help="Where to write the judge JSON result.")
    parser.add_argument(
        "--config",
        default="generative_agents/data/config.json",
        help="Project config used to resolve the default LLM provider/model.",
    )
    parser.add_argument("--provider", default="", help="Override provider from config, e.g. minimax.")
    parser.add_argument("--model", default="", help="Override model from config.")
    parser.add_argument("--base-url", default="", help="Override OpenAI-compatible base URL.")
    parser.add_argument("--api-key", default="", help="Override API key. Prefer environment variables.")
    parser.add_argument("--temperature", type=float, default=0.1, help="Judge temperature.")
    parser.add_argument("--max-evidence-rows", type=int, default=12, help="Rows kept per evidence type.")
    parser.add_argument("--dry-run", action="store_true", help="Write the judge prompt instead of calling the LLM.")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_llm_config(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    config_path = (repo_root / args.config).resolve()
    config = load_json(config_path)
    llm_config = config["agent"]["think"]["llm"]
    provider = args.provider or llm_config.get("provider", "")
    provider_env = provider.upper()
    return {
        "provider": provider,
        "model": args.model or llm_config.get("model", ""),
        "base_url": (args.base_url or llm_config.get("base_url", "")).rstrip("/"),
        "api_key": args.api_key or llm_config.get("api_key", "") or os.getenv(f"{provider_env}_API_KEY", ""),
        "max_tokens": llm_config.get("max_tokens", 8192),
    }


def compact_rows(rows: list[dict[str, Any]], max_rows: int, prefix: str) -> list[dict[str, Any]]:
    compacted = []
    for index, row in enumerate(rows[:max_rows]):
        compacted.append({"evidence_id": f"{prefix}:{index}", **row})
    return compacted


def build_evidence_bundle(metrics: dict[str, Any], max_rows: int) -> dict[str, Any]:
    action_metrics = metrics.get("action_metrics", {})
    social_metrics = metrics.get("social_metrics", {})
    commitment_metrics = metrics.get("commitment_metrics", {})
    memory_metrics = metrics.get("memory_metrics", {})
    conversation_metrics = metrics.get("conversation_metrics", {})

    return {
        "simulation": metrics.get("simulation"),
        "agent": metrics.get("agent"),
        "latest_checkpoint": metrics.get("latest_checkpoint"),
        "checkpoint_time": metrics.get("checkpoint_time"),
        "window": metrics.get("window"),
        "goal_keywords": metrics.get("goal_keywords", []),
        "fact_keywords": metrics.get("fact_keywords", []),
        "location_keywords": metrics.get("location_keywords", []),
        "metric_summary": {
            "schedule_metrics": metrics.get("schedule_metrics", {}),
            "action_metrics": {
                key: value
                for key, value in action_metrics.items()
                if key != "evidence_rows"
            },
            "commitment_metrics": {
                key: value
                for key, value in commitment_metrics.items()
                if key != "evidence_rows"
            },
            "conversation_metrics": {
                key: value
                for key, value in conversation_metrics.items()
                if key != "evidence_rows"
            },
            "social_metrics": {
                key: value
                for key, value in social_metrics.items()
                if key != "evidence_rows"
            },
            "memory_metrics": {
                key: value
                for key, value in memory_metrics.items()
                if key != "examples"
            },
        },
        "evidence": {
            "actions": compact_rows(action_metrics.get("evidence_rows", []), max_rows, "action"),
            "social": compact_rows(social_metrics.get("evidence_rows", []), max_rows, "social"),
            "commitments": compact_rows(commitment_metrics.get("evidence_rows", []), max_rows, "commitment"),
            "memory": compact_rows(memory_metrics.get("examples", []), max_rows, "memory"),
            "conversations": compact_rows(conversation_metrics.get("evidence_rows", []), max_rows, "conversation"),
        },
    }


def build_prompt(bundle: dict[str, Any]) -> str:
    schema = {
        "simulation": "string",
        "agent": "string",
        "judge_method": "LLM as Judge with evidence-constrained rubric",
        "scores": [
            {
                "dimension": "string",
                "score": "integer 1-5",
                "confidence": "number 0-1",
                "evidence_ids": ["string"],
                "rationale": "string",
                "risks": ["string"],
                "needs_human_review": "boolean",
            }
        ],
        "overall_score": "number",
        "summary": "string",
        "follow_up_checks": ["string"],
    }
    rubric_text = "\n".join(
        [
            (
                f"- {rubric['label']}: 问题={rubric['question']} "
                f"5分倾向={rubric['strong']} 1分倾向={rubric['weak']}"
            )
            for rubric in DIMENSION_RUBRICS.values()
        ]
    )
    return (
        "你是一个用于评价生成式智能体 Generative Agents 仿真结果的 LLM as Judge。\n"
        "只能根据输入的 evidence_bundle 判断，不允许引入外部事实，不允许猜测没有证据支持的事件。\n"
        "请按 1-5 分评价每个维度：1=严重不可信，3=基本可解释但有明显瑕疵，5=证据高度一致。\n"
        "每个评分必须引用 evidence_id；如果证据不足，降低 confidence，并设置 needs_human_review=true。\n"
        "不要输出 markdown，不要输出解释性前言，只输出 JSON。\n\n"
        f"评分 rubric:\n{rubric_text}\n\n"
        f"输出 JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"evidence_bundle:\n{json.dumps(bundle, ensure_ascii=False, indent=2)}"
    )


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group())


def call_openai_compatible(prompt: str, llm_config: dict[str, Any], temperature: float) -> dict[str, Any]:
    if not llm_config["api_key"]:
        raise RuntimeError(
            f"{llm_config['provider']} API key is required. Set {llm_config['provider'].upper()}_API_KEY."
        )
    url = f"{llm_config['base_url']}/chat/completions"
    response = requests.post(
        url=url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {llm_config['api_key']}",
        },
        json={
            "model": llm_config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": llm_config["max_tokens"],
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=300,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Judge API returned {response.status_code}: {response.text[:500]}")
    data = response.json()
    content = data["choices"][0]["message"].get("content") or ""
    result = parse_json_response(content)
    result["_judge_usage"] = data.get("usage", {})
    return result


def add_overall_if_missing(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores", [])
    numeric_scores = [
        item.get("score")
        for item in scores
        if isinstance(item.get("score"), (int, float))
    ]
    if numeric_scores and not isinstance(result.get("overall_score"), (int, float)):
        result["overall_score"] = statistics.mean(numeric_scores)
    return result


def main() -> None:
    args = parse_args()
    repo_root = Path(".").resolve()
    metrics_path = (repo_root / args.metrics_json).resolve()
    output_path = (repo_root / args.output).resolve()
    metrics = load_json(metrics_path)
    bundle = build_evidence_bundle(metrics, args.max_evidence_rows)
    prompt = build_prompt(bundle)

    if args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prompt, encoding="utf-8")
        print(json.dumps({"prompt": str(output_path), "dry_run": True}, ensure_ascii=False, indent=2))
        return

    llm_config = resolve_llm_config(args, repo_root)
    result = call_openai_compatible(prompt, llm_config, args.temperature)
    result = add_overall_if_missing(result)
    result["_judge_config"] = {
        "provider": llm_config["provider"],
        "model": llm_config["model"],
        "temperature": args.temperature,
        "metrics_json": str(metrics_path.relative_to(repo_root)),
    }
    write_json(output_path, result)
    summary = {
        "simulation": result.get("simulation"),
        "agent": result.get("agent"),
        "overall_score": result.get("overall_score"),
        "score_count": len(result.get("scores", [])),
        "output": str(output_path.relative_to(repo_root)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
