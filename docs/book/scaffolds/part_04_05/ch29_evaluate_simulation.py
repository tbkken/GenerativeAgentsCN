#!/usr/bin/env python
"""Chapter 29 evidence evaluator.

This script reads one completed Generative Agents simulation and turns
checkpoint, movement, conversation, and memory files into reproducible metrics.
It is intentionally lightweight: no LLM calls, no external dependencies.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_GENERIC_ACTIONS = {
    "工作",
    "学习",
    "休息",
    "社交",
    "做自己的事情",
    "思考下一步计划",
    "处理事务",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate one simulation result for chapter 29.")
    parser.add_argument("--name", required=True, help="Simulation name under generative_agents/results.")
    parser.add_argument("--agent", required=True, help="Agent name to evaluate.")
    parser.add_argument("--window-start", required=True, help="Window start time, e.g. 15:50.")
    parser.add_argument("--window-end", required=True, help="Window end time, e.g. 16:50.")
    parser.add_argument("--sample-minutes", type=int, default=10, help="Sampling interval in minutes.")
    parser.add_argument(
        "--goal-keywords",
        required=True,
        help="Comma-separated keywords that define goal-related actions.",
    )
    parser.add_argument(
        "--source-agent",
        default="",
        help="Optional source agent for social diffusion metrics.",
    )
    parser.add_argument(
        "--fact-keywords",
        default="",
        help="Comma-separated core facts for fact preservation. Defaults to goal keywords.",
    )
    parser.add_argument(
        "--invitation-keywords",
        default="邀请,参加,四点,下午4点,下午 4 点,到场,记得来,一起来,来参加,能来,过来",
        help="Comma-separated words that indicate an invitation or commitment.",
    )
    parser.add_argument(
        "--location-keywords",
        required=True,
        help="Comma-separated keywords that define acceptable locations.",
    )
    parser.add_argument(
        "--commitments",
        default="",
        help=(
            "Optional semicolon-separated commitments in the format "
            "'agent|HH:MM|location_keyword1+location_keyword2|description'."
        ),
    )
    parser.add_argument(
        "--arrival-tolerance-minutes",
        type=int,
        default=20,
        help="Allowed minutes before/after a promised time for commitment arrival checks.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path. Parent folders are created automatically.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root. Defaults to current working directory.",
    )
    return parser.parse_args()


def split_keywords(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


def minutes_since_midnight(value: str) -> int:
    hour, minute = parse_hhmm(value)
    return hour * 60 + minute


def hhmm_from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def latest_checkpoint(checkpoint_dir: Path) -> Path:
    files = sorted(checkpoint_dir.glob("simulate-*.json"))
    if not files:
        raise FileNotFoundError(f"No simulate-*.json files found in {checkpoint_dir}")
    return files[-1]


def schedule_conflicts(schedule: list[dict[str, Any]]) -> dict[str, int]:
    invalid_duration_count = 0
    overlap_count = 0
    boundary_error_count = 0

    for plan in schedule:
        start = int(plan["start"])
        end = start + int(plan["duration"])
        if int(plan["duration"]) <= 0 or start < 0 or end > 1440:
            invalid_duration_count += 1

    for current, nxt in zip(schedule, schedule[1:]):
        current_end = int(current["start"]) + int(current["duration"])
        next_start = int(nxt["start"])
        if next_start < current_end:
            overlap_count += 1

    if schedule:
        first_start = int(schedule[0]["start"])
        last_end = int(schedule[-1]["start"]) + int(schedule[-1]["duration"])
        boundary_error_count += int(first_start != 0)
        boundary_error_count += int(last_end != 1440)

    total = invalid_duration_count + overlap_count + boundary_error_count
    return {
        "invalid_duration_count": invalid_duration_count,
        "overlap_count": overlap_count,
        "boundary_error_count": boundary_error_count,
        "schedule_conflict_count": total,
    }


def schedule_coverage_rate(schedule: list[dict[str, Any]], start_minute: int, end_minute: int) -> float:
    covered = 0
    for plan in schedule:
        plan_start = int(plan["start"])
        plan_end = plan_start + int(plan["duration"])
        overlap_start = max(start_minute, plan_start)
        overlap_end = min(end_minute, plan_end)
        if overlap_end > overlap_start:
            covered += overlap_end - overlap_start
    window = max(end_minute - start_minute, 1)
    return covered / window


def current_plan(schedule: list[dict[str, Any]], minute: int) -> dict[str, Any] | None:
    for plan in schedule:
        start = int(plan["start"])
        end = start + int(plan["duration"])
        if start <= minute < end:
            return plan
    return schedule[-1] if schedule else None


SEMANTIC_STOP_TERMS = {
    "一个",
    "一些",
    "这个",
    "那个",
    "自己",
    "进行",
    "继续",
    "关于",
    "一下",
    "可以",
    "需要",
    "准备",
}


def token_set(text: str) -> set[str]:
    ascii_tokens = re.findall(r"[A-Za-z0-9_]+", text or "")
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text or "")
    chinese_bigrams = {
        "".join(chinese_chars[index : index + 2])
        for index in range(max(len(chinese_chars) - 1, 0))
    }
    return {
        token
        for token in [*ascii_tokens, *chinese_bigrams]
        if token and token not in SEMANTIC_STOP_TERMS
    }


def semantic_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    return len(left_tokens & right_tokens) >= 2


def is_generic_action(action: str, generic_actions: set[str] = DEFAULT_GENERIC_ACTIONS) -> bool:
    normalized = (action or "").strip()
    if normalized in generic_actions:
        return True
    return any(term == normalized for term in generic_actions)


def frame_for_time(movement: dict[str, Any], hhmm: str) -> int:
    start = dt.datetime.fromisoformat(movement["start_datetime"])
    hour, minute = parse_hhmm(hhmm)
    target = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < start:
        target += dt.timedelta(days=1)
    delta_seconds = int((target - start).total_seconds())
    return delta_seconds // int(movement.get("sec_per_step", 10))


def reconstruct_state(movement: dict[str, Any], agent: str, frame: int) -> dict[str, Any]:
    all_movement = movement.get("all_movement", {})
    frames = sorted(int(k) for k in all_movement if str(k).isdigit())
    state = {
        "location": None,
        "movement": movement.get("persona_init_pos", {}).get(agent),
        "description": None,
        "action": None,
        "chat": None,
    }
    for item_frame in frames:
        if item_frame > frame:
            break
        update = all_movement.get(str(item_frame), {})
        if agent in update:
            state.update(update[agent])
    return state


def sample_actions(
    movement: dict[str, Any],
    agent: str,
    start_hhmm: str,
    end_hhmm: str,
    sample_minutes: int,
) -> list[dict[str, Any]]:
    start_minute = minutes_since_midnight(start_hhmm)
    end_minute = minutes_since_midnight(end_hhmm)
    if end_minute < start_minute:
        end_minute += 24 * 60

    rows = []
    for minute in range(start_minute, end_minute + 1, sample_minutes):
        label = hhmm_from_minutes(minute % (24 * 60))
        frame = frame_for_time(movement, label)
        state = reconstruct_state(movement, agent, frame)
        action = state.get("action") or state.get("description") or ""
        rows.append(
            {
                "time": label,
                "frame": frame,
                "location": state.get("location") or "",
                "movement": state.get("movement"),
                "action": action,
            }
        )
    return rows


def parse_commitments(value: str) -> list[dict[str, Any]]:
    commitments = []
    for raw_item in [item.strip() for item in value.split(";") if item.strip()]:
        parts = [part.strip() for part in raw_item.split("|", 3)]
        if len(parts) != 4:
            raise ValueError(
                "Each commitment must use 'agent|HH:MM|location_keyword1+location_keyword2|description'."
            )
        agent, promised_time, location_text, description = parts
        commitments.append(
            {
                "agent": agent,
                "promised_time": promised_time,
                "location_keywords": [part.strip() for part in location_text.split("+") if part.strip()],
                "description": description,
            }
        )
    return commitments


def commitment_metrics(
    movement: dict[str, Any],
    commitments: list[dict[str, Any]],
    tolerance_minutes: int,
) -> dict[str, Any]:
    if not commitments:
        return {
            "commitment_count": 0,
            "arrived_commitment_count": 0,
            "arrival_rate": None,
            "mean_abs_arrival_delay_minutes": None,
            "evidence_rows": [],
        }

    arrived_count = 0
    delays: list[int] = []
    evidence_rows = []
    for commitment in commitments:
        promised_minute = minutes_since_midnight(commitment["promised_time"])
        start = promised_minute - tolerance_minutes
        end = promised_minute + tolerance_minutes
        best_hit = None
        sampled_rows = []
        for minute in range(start, end + 1, 5):
            label = hhmm_from_minutes(minute % (24 * 60))
            frame = frame_for_time(movement, label)
            state = reconstruct_state(movement, commitment["agent"], frame)
            location = state.get("location") or ""
            hit = any(keyword in location for keyword in commitment["location_keywords"])
            sampled_rows.append({"time": label, "location": location, "hit": hit})
            delay = abs(minute - promised_minute)
            if hit and (best_hit is None or delay < best_hit["delay_minutes"]):
                best_hit = {
                    "time": label,
                    "location": location,
                    "delay_minutes": delay,
                }

        arrived = best_hit is not None
        arrived_count += int(arrived)
        if best_hit:
            delays.append(best_hit["delay_minutes"])
        evidence_rows.append(
            {
                **commitment,
                "arrived": arrived,
                "matched_time": best_hit["time"] if best_hit else None,
                "matched_location": best_hit["location"] if best_hit else None,
                "delay_minutes": best_hit["delay_minutes"] if best_hit else None,
                "sampled_rows": sampled_rows,
            }
        )

    denominator = max(len(commitments), 1)
    return {
        "commitment_count": len(commitments),
        "arrived_commitment_count": arrived_count,
        "arrival_rate": arrived_count / denominator,
        "mean_abs_arrival_delay_minutes": sum(delays) / len(delays) if delays else None,
        "arrival_tolerance_minutes": tolerance_minutes,
        "evidence_rows": evidence_rows,
    }


def action_metrics(
    actions: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    goal_keywords: list[str],
    location_keywords: list[str],
) -> dict[str, Any]:
    n = max(len(actions), 1)
    location_hits = 0
    goal_hits = 0
    generic_hits = 0
    repeated_generic_count = 0
    plan_action_hits = 0
    last_generic_action = None

    evidence_rows = []
    for row in actions:
        action = row["action"]
        location = row["location"]
        minute = minutes_since_midnight(row["time"])
        plan = current_plan(schedule, minute)
        plan_desc = plan.get("describe", "") if plan else ""
        location_match = any(keyword in location for keyword in location_keywords)
        goal_match = any(keyword in action for keyword in goal_keywords)
        generic_match = is_generic_action(action)
        plan_match = semantic_match(plan_desc, action)

        location_hits += int(location_match)
        goal_hits += int(goal_match)
        generic_hits += int(generic_match)
        plan_action_hits += int(plan_match)
        if generic_match and action == last_generic_action:
            repeated_generic_count += 1
        last_generic_action = action if generic_match else None

        evidence_rows.append(
            {
                **row,
                "current_plan": plan_desc,
                "location_match": location_match,
                "goal_match": goal_match,
                "generic_match": generic_match,
                "plan_action_match": plan_match,
            }
        )

    return {
        "sample_count": len(actions),
        "location_match_count": location_hits,
        "location_match_rate": location_hits / n,
        "goal_related_action_count": goal_hits,
        "goal_related_action_rate": goal_hits / n,
        "generic_action_count": generic_hits,
        "generic_action_rate": generic_hits / n,
        "repeated_generic_action_count": repeated_generic_count,
        "plan_action_match_count": plan_action_hits,
        "plan_action_match_rate": plan_action_hits / n,
        "evidence_rows": evidence_rows,
    }


def conversation_metrics(conversation_path: Path, agent: str, goal_keywords: list[str]) -> dict[str, Any]:
    if not conversation_path.exists():
        return {"conversation_count": 0, "agent_conversation_count": 0, "json_residue_count": 0}

    data = load_json(conversation_path)
    conversation_count = 0
    agent_conversation_count = 0
    json_residue_count = 0
    keyword_mentions = collections.Counter()
    partners = set()
    rows = []

    for time_key, items in data.items():
        for item in items:
            for title, lines in item.items():
                conversation_count += 1
                title_has_agent = agent in title
                line_text = "\n".join(text for _, text in lines)
                if title_has_agent:
                    agent_conversation_count += 1
                    names = re.split(r"\s*->\s*|\s*@\s*", title)[0:2]
                    for name in names:
                        cleaned = name.strip()
                        if cleaned and cleaned != agent:
                            partners.add(cleaned)
                json_residue_count += line_text.count('"res"')
                for keyword in goal_keywords:
                    keyword_mentions[keyword] += line_text.count(keyword)
                if title_has_agent or any(keyword in line_text for keyword in goal_keywords):
                    rows.append(
                        {
                            "time": time_key,
                            "title": title,
                            "line_count": len(lines),
                            "first_line": lines[0][1] if lines else "",
                        }
                    )

    return {
        "conversation_count": conversation_count,
        "agent_conversation_count": agent_conversation_count,
        "unique_agent_partners": sorted(partners),
        "json_residue_count": json_residue_count,
        "goal_keyword_mentions": dict(keyword_mentions),
        "evidence_rows": rows[:20],
    }


def parse_participants(title: str) -> list[str]:
    before_location = title.split("@", 1)[0]
    if "->" not in before_location:
        return []
    return [part.strip() for part in before_location.split("->", 1) if part.strip()]


def classify_attitude(text: str) -> str:
    positive = ["好", "可以", "没问题", "愿意", "一定", "参加", "支持", "赞成", "期待"]
    negative = ["不能", "没空", "拒绝", "反对", "怀疑", "担心", "不喜欢", "不同意", "不去"]
    uncertain = ["可能", "也许", "看看", "考虑", "不确定", "有点", "怕"]
    if any(word in text for word in uncertain):
        return "uncertain"
    if any(word in text for word in negative):
        return "negative"
    if any(word in text for word in positive):
        return "positive"
    return "neutral"


def social_metrics(
    conversation_path: Path,
    source_agent: str,
    goal_keywords: list[str],
    fact_keywords: list[str],
    invitation_keywords: list[str],
) -> dict[str, Any]:
    if not conversation_path.exists():
        return {"exists": False}

    data = load_json(conversation_path)
    informed_agents: set[str] = set()
    direct_targets: set[str] = set()
    source_mention_targets: set[str] = set()
    indirect_mentions = 0
    fact_related_mentions = 0
    preserved_fact_mentions = 0
    edges: set[tuple[str, str]] = set()
    attitudes: set[str] = set()
    evidence_rows: list[dict[str, Any]] = []

    for time_key, items in data.items():
        for item in items:
            for title, lines in item.items():
                participants = parse_participants(title)
                if len(participants) < 2:
                    continue
                for speaker, text in lines:
                    has_goal = any(keyword in text for keyword in goal_keywords)
                    has_fact = any(keyword in text for keyword in fact_keywords)
                    if not (has_goal or has_fact):
                        continue

                    fact_related_mentions += 1
                    if fact_keywords and all(keyword in text or keyword in title for keyword in fact_keywords):
                        preserved_fact_mentions += 1

                    informed_agents.add(speaker)
                    for participant in participants:
                        if participant != speaker:
                            informed_agents.add(participant)
                            edges.add((speaker, participant))

                    if source_agent and speaker == source_agent:
                        for participant in participants:
                            if participant != source_agent:
                                source_mention_targets.add(participant)
                                if any(keyword in text for keyword in invitation_keywords):
                                    direct_targets.add(participant)
                    elif source_agent:
                        indirect_mentions += 1

                    attitude = classify_attitude(text)
                    if attitude != "neutral":
                        attitudes.add(attitude)

                    if len(evidence_rows) < 20:
                        evidence_rows.append(
                            {
                                "time": time_key,
                                "title": title,
                                "speaker": speaker,
                                "attitude": attitude,
                                "text": text[:180],
                            }
                        )

    depth = diffusion_depth(source_agent, edges) if source_agent else 0
    denominator = max(fact_related_mentions, 1)
    attitude_denominator = 3
    return {
        "exists": True,
        "source_agent": source_agent or None,
        "unique_informed_agents": len(informed_agents),
        "informed_agents": sorted(informed_agents),
        "source_mention_count": len(source_mention_targets),
        "source_mention_targets": sorted(source_mention_targets),
        "direct_invitation_count": len(direct_targets),
        "direct_invitation_targets": sorted(direct_targets),
        "indirect_mention_count": indirect_mentions,
        "diffusion_depth": depth,
        "fact_related_mentions": fact_related_mentions,
        "preserved_fact_mentions": preserved_fact_mentions,
        "fact_preservation_score": preserved_fact_mentions / denominator,
        "attitude_labels": sorted(attitudes),
        "attitude_diversity_score": len(attitudes) / attitude_denominator,
        "diffusion_edges": sorted([list(edge) for edge in edges]),
        "evidence_rows": evidence_rows,
    }


def diffusion_depth(source_agent: str, edges: set[tuple[str, str]]) -> int:
    if not source_agent:
        return 0
    graph: dict[str, set[str]] = collections.defaultdict(set)
    for src, dst in edges:
        graph[src].add(dst)
    seen = {source_agent}
    queue = collections.deque([(source_agent, 0)])
    max_depth = 0
    while queue:
        node, depth = queue.popleft()
        max_depth = max(max_depth, depth)
        for nxt in graph.get(node, set()):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, depth + 1))
    return max_depth


def memory_metrics(storage_dir: Path, agent: str, goal_keywords: list[str]) -> dict[str, Any]:
    docstore = storage_dir / agent / "associate" / "docstore.json"
    if not docstore.exists():
        return {"exists": False}
    data = load_json(docstore).get("docstore/data", {})
    node_type_counts = collections.Counter()
    goal_related_nodes = 0
    examples = []

    for node_id, node in data.items():
        payload = node.get("__data__", {})
        metadata = payload.get("metadata", {})
        text = payload.get("text", "")
        node_type = metadata.get("node_type", "unknown")
        node_type_counts[node_type] += 1
        if any(keyword in text for keyword in goal_keywords):
            goal_related_nodes += 1
            if len(examples) < 8:
                examples.append(
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "create": metadata.get("create"),
                        "address": metadata.get("address"),
                        "text": text[:180],
                    }
                )

    return {
        "exists": True,
        "total_nodes": len(data),
        "node_type_counts": dict(node_type_counts),
        "goal_related_nodes": goal_related_nodes,
        "examples": examples,
    }


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    result_root = repo_root / "generative_agents" / "results"
    checkpoint_dir = result_root / "checkpoints" / args.name
    compressed_dir = result_root / "compressed" / args.name
    checkpoint_path = latest_checkpoint(checkpoint_dir)
    checkpoint = load_json(checkpoint_path)
    movement = load_json(compressed_dir / "movement.json")

    goal_keywords = split_keywords(args.goal_keywords)
    fact_keywords = split_keywords(args.fact_keywords) or goal_keywords
    invitation_keywords = split_keywords(args.invitation_keywords)
    location_keywords = split_keywords(args.location_keywords)
    commitments = parse_commitments(args.commitments)
    agent_data = checkpoint["agents"][args.agent]
    schedule = agent_data["schedule"]["daily_schedule"]
    start_minute = minutes_since_midnight(args.window_start)
    end_minute = minutes_since_midnight(args.window_end)
    if end_minute < start_minute:
        end_minute += 24 * 60

    actions = sample_actions(
        movement,
        args.agent,
        args.window_start,
        args.window_end,
        args.sample_minutes,
    )
    result = {
        "simulation": args.name,
        "agent": args.agent,
        "latest_checkpoint": str(checkpoint_path.relative_to(repo_root)),
        "checkpoint_time": checkpoint.get("time"),
        "checkpoint_step": checkpoint.get("step"),
        "window": {
            "start": args.window_start,
            "end": args.window_end,
            "sample_minutes": args.sample_minutes,
        },
        "goal_keywords": goal_keywords,
        "fact_keywords": fact_keywords,
        "location_keywords": location_keywords,
        "schedule_metrics": {
            **schedule_conflicts(schedule),
            "schedule_coverage_rate": schedule_coverage_rate(schedule, start_minute, end_minute),
        },
        "action_metrics": action_metrics(actions, schedule, goal_keywords, location_keywords),
        "commitment_metrics": commitment_metrics(movement, commitments, args.arrival_tolerance_minutes),
        "conversation_metrics": conversation_metrics(checkpoint_dir / "conversation.json", args.agent, goal_keywords),
        "social_metrics": social_metrics(
            checkpoint_dir / "conversation.json",
            args.source_agent,
            goal_keywords,
            fact_keywords,
            invitation_keywords,
        ),
        "memory_metrics": memory_metrics(checkpoint_dir / "storage", args.agent, goal_keywords),
    }

    if args.output:
        output = (repo_root / args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "simulation": result["simulation"],
        "agent": result["agent"],
        "schedule_conflict_count": result["schedule_metrics"]["schedule_conflict_count"],
        "location_match_rate": result["action_metrics"]["location_match_rate"],
        "goal_related_action_rate": result["action_metrics"]["goal_related_action_rate"],
        "plan_action_match_rate": result["action_metrics"]["plan_action_match_rate"],
        "arrival_rate": result["commitment_metrics"].get("arrival_rate"),
        "unique_informed_agents": result["social_metrics"].get("unique_informed_agents"),
        "diffusion_depth": result["social_metrics"].get("diffusion_depth"),
        "fact_preservation_score": result["social_metrics"].get("fact_preservation_score"),
        "json_residue_count": result["conversation_metrics"]["json_residue_count"],
        "memory_goal_related_nodes": result["memory_metrics"].get("goal_related_nodes"),
        "output": args.output or None,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
