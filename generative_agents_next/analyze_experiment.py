import argparse
import json
import os
import re
from datetime import datetime, timedelta


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(value):
    return datetime.strptime(value, "%Y%m%d-%H:%M")


def split_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def checkpoint_files(checkpoints_dir):
    if not os.path.isdir(checkpoints_dir):
        return []
    return [
        os.path.join(checkpoints_dir, name)
        for name in sorted(os.listdir(checkpoints_dir))
        if name.startswith("simulate-") and name.endswith(".json")
    ]


def latest_checkpoint(checkpoints_dir):
    files = checkpoint_files(checkpoints_dir)
    return load_json(files[-1], {}) if files else {}


def flatten_conversation(conversation):
    rows = []
    for time_key, blocks in sorted(conversation.items()):
        for block in blocks:
            for route, turns in block.items():
                for speaker, text in turns:
                    rows.append(
                        {
                            "time": time_key,
                            "route": route,
                            "speaker": speaker,
                            "text": text,
                        }
                    )
    return rows


def detect_commitment(text):
    invitation_patterns = [
        r"一起过来",
        r"记得来",
        r"来玩",
        r"欢迎.{0,8}来",
    ]
    accept_patterns = [
        r"我.{0,30}直播完.{0,20}(过来|到|来|捧场)",
        r"直播完.{0,20}(过来|到|来|捧场)",
        r"我.{0,30}(一定|肯定|会|可以|能).{0,20}(过来|到场|参加|捧场)",
        r"我.{0,30}(五点|六点|17:00|18:00|下午|晚上).{0,20}(过来|到场|到|参加|捧场)",
        r"(肯定|一定).{0,8}(过来|到场|参加|捧场)",
    ]
    reject_patterns = [
        r"(不能|没法|有约|来不了|赶不上|冲突|抱歉)",
        r"(我不行|我今天不行|今天不行|下午不行|晚上不行|可能不行)",
    ]
    if any(re.search(pattern, text) for pattern in reject_patterns):
        return "rejected"
    if any(re.search(pattern, text) for pattern in invitation_patterns):
        return ""
    if any(re.search(pattern, text) for pattern in accept_patterns):
        return "accepted"
    return ""


def collect_mentions(rows, keywords):
    mentions = []
    for row in rows:
        hits = [keyword for keyword in keywords if keyword and keyword in row["text"]]
        if not hits:
            continue
        commitment = detect_commitment(row["text"])
        mentions.append(
            {
                "time": row["time"],
                "route": row["route"],
                "speaker": row["speaker"],
                "keywords": hits,
                "commitment": commitment,
                "text": row["text"],
            }
        )
    return mentions


def movement_rows(movement):
    rows = []
    all_movement = movement.get("all_movement", {})
    start_datetime = movement.get("start_datetime")
    stride = int(movement.get("stride", 10) or 10)
    start = datetime.fromisoformat(start_datetime) if start_datetime else None
    for frame, agents in all_movement.items():
        if frame in ("description", "conversation"):
            continue
        frame_num = int(frame)
        frame_time = None
        if start:
            frame_time = start + timedelta(minutes=(max(frame_num - 1, 0) * stride / 60))
        for agent, state in agents.items():
            rows.append(
                {
                    "frame": frame_num,
                    "time": frame_time.strftime("%Y%m%d-%H:%M") if frame_time else "",
                    "agent": agent,
                    "location": state.get("location", ""),
                    "action": state.get("action", ""),
                }
            )
    return rows


def collect_attendance(movement, target_place, window_start=None, window_end=None):
    if not target_place:
        return []
    arrivals = {}
    start = parse_time(window_start) if window_start else None
    end = parse_time(window_end) if window_end else None
    for row in movement_rows(movement):
        if target_place not in row["location"]:
            continue
        if row["time"]:
            row_time = parse_time(row["time"])
            if start and row_time < start:
                continue
            if end and row_time > end:
                continue
        arrivals.setdefault(row["agent"], row)
    return list(arrivals.values())


def collect_memory_summary(final_state):
    agents = final_state.get("agents", {})
    summary = {}
    for name, state in agents.items():
        memory = state.get("associate", {}).get("memory", {})
        summary[name] = {
            key: len(value) if isinstance(value, list) else 0
            for key, value in sorted(memory.items())
        }
    return summary


def build_event_board(event_name, mentions, attendance):
    known_by = sorted({row["speaker"] for row in mentions})
    accepted = sorted({row["speaker"] for row in mentions if row["commitment"] == "accepted"})
    rejected = sorted({row["speaker"] for row in mentions if row["commitment"] == "rejected"})
    arrived = sorted({row["agent"] for row in attendance})
    tasks = [
        {
            "task_id": "spread_fact",
            "status": "done" if known_by else "pending",
            "owners": known_by,
            "evidence": [row["time"] for row in mentions],
        },
        {
            "task_id": "collect_commitments",
            "status": "done" if accepted or rejected else "pending",
            "owners": accepted + rejected,
            "accepted": accepted,
            "rejected": rejected,
        },
        {
            "task_id": "verify_attendance",
            "status": "done" if arrived else "pending",
            "owners": arrived,
        },
    ]
    return {
        "event": event_name,
        "known_by": known_by,
        "accepted": accepted,
        "rejected": rejected,
        "arrived": arrived,
        "tasks": tasks,
    }


def build_reflection_candidates(event_board, mentions):
    candidates = []
    arrived = set(event_board["arrived"])
    for speaker in event_board["accepted"]:
        if speaker in arrived:
            continue
        evidence = [
            {
                "time": row["time"],
                "route": row["route"],
                "text": row["text"],
            }
            for row in mentions
            if row["speaker"] == speaker and row["commitment"] == "accepted"
        ]
        candidates.append(
            {
                "agent": speaker,
                "outcome": "failed",
                "failure_type": "commitment_not_verified_by_movement",
                "lesson": "承诺类对话需要在目标时间窗用 movement.json 复核到场，而不是只相信聊天摘要。",
                "evidence": evidence,
            }
        )
    return candidates


def build_goal_progress(event_board):
    accepted = set(event_board["accepted"])
    rejected = set(event_board["rejected"])
    arrived = set(event_board["arrived"])
    informed = set(event_board["known_by"])
    missing = []
    accepted_not_arrived = sorted(accepted - arrived)
    if not informed:
        missing.append("没有角色在对话中命中事件关键词。")
    if accepted_not_arrived:
        missing.append(
            "这些角色有承诺但未在目标时间窗到场：" + "、".join(accepted_not_arrived)
        )
    if not arrived:
        missing.append("目标时间窗内没有角色到达目标地点。")
    if rejected:
        missing.append("这些角色明确拒绝或表示时间冲突：" + "、".join(sorted(rejected)))
    criteria = {
        "has_event_diffusion": bool(informed),
        "has_commitment": bool(accepted),
        "has_attendance": bool(arrived),
    }
    return {
        "informed": sorted(informed),
        "accepted": sorted(accepted),
        "arrived": sorted(arrived),
        "rejected_or_unavailable": sorted(rejected),
        "accepted_not_arrived": accepted_not_arrived,
        "missing": missing,
        "criteria": criteria,
        "goal_completion_rate": round(
            sum(1 for value in criteria.values() if value) / len(criteria), 4
        ),
    }


def summarize_batch(evaluation_root, names, output_dir):
    rows = []
    for name in names:
        metrics_path = os.path.join(evaluation_root, name, "metrics.json")
        metrics = load_json(metrics_path, {})
        if not metrics:
            rows.append({"experiment": name, "status": "missing_metrics"})
            continue
        rows.append(
            {
                "experiment": name,
                "status": "ok",
                "mentions": metrics.get("diffusion", {}).get("mention_count", 0),
                "known_agents": metrics.get("diffusion", {}).get("known_agent_count", 0),
                "accepted": metrics.get("commitments", {}).get("accepted_count", 0),
                "rejected": metrics.get("commitments", {}).get("rejected_count", 0),
                "arrived": metrics.get("attendance", {}).get("arrived_count", 0),
                "goal_completion_rate": metrics.get("goal_progress", {}).get(
                    "goal_completion_rate", 0
                ),
                "final_time": metrics.get("final_time", ""),
            }
        )
    ok_rows = [row for row in rows if row["status"] == "ok"]

    def _avg(key):
        if not ok_rows:
            return 0
        return round(sum(row.get(key, 0) for row in ok_rows) / len(ok_rows), 4)

    summary = {
        "runs": rows,
        "run_count": len(names),
        "successful_metric_files": len(ok_rows),
        "averages": {
            "mentions": _avg("mentions"),
            "known_agents": _avg("known_agents"),
            "accepted": _avg("accepted"),
            "rejected": _avg("rejected"),
            "arrived": _avg("arrived"),
            "goal_completion_rate": _avg("goal_completion_rate"),
        },
    }
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def write_report(path, metrics, mentions, attendance, event_board, reflection_candidates, goal_progress):
    lines = [
        "# 实验评价报告",
        "",
        f"实验名：`{metrics['experiment']}`",
        f"事件：`{metrics['event']}`",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 对话命中 mentions | {metrics['diffusion']['mention_count']} |",
        f"| 知情角色 known_agents | {metrics['diffusion']['known_agent_count']} |",
        f"| 接受承诺 accepted_commitments | {metrics['commitments']['accepted_count']} |",
        f"| 拒绝承诺 rejected_commitments | {metrics['commitments']['rejected_count']} |",
        f"| 到场角色 arrived_agents | {metrics['attendance']['arrived_count']} |",
        f"| 反思候选 reflection_candidates | {len(reflection_candidates)} |",
        f"| 目标完成率 goal_completion_rate | {goal_progress['goal_completion_rate']} |",
        "",
        "## 传播证据",
        "",
    ]
    for row in mentions[:20]:
        lines.append(f"- `{row['time']}` `{row['speaker']}`：{row['text']}")
    if not mentions:
        lines.append("- 未命中事件关键词。")
    lines.extend(["", "## 到场证据", ""])
    for row in attendance[:20]:
        lines.append(f"- `{row['time']}` frame `{row['frame']}` `{row['agent']}` @ {row['location']}：{row['action']}")
    if not attendance:
        lines.append("- 未在 movement.json 中找到目标地点到场证据。")
    lines.extend(["", "## 目标进度", "", "```json", json.dumps(goal_progress, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 事件板", "", "```json", json.dumps(event_board, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 反思候选", "", "```json", json.dumps(reflection_candidates, ensure_ascii=False, indent=2), "```"])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze a GenerativeAgentsCN experiment without LLM calls.")
    parser.add_argument("--name", required=True, help="Experiment name under results/checkpoints")
    parser.add_argument("--event", default="event", help="Event name for reports")
    parser.add_argument("--keywords", default="", help="Comma-separated event keywords")
    parser.add_argument("--target-place", default="", help="Location substring used for attendance")
    parser.add_argument("--window-start", default="", help="Attendance window start, e.g. 20240214-17:00")
    parser.add_argument("--window-end", default="", help="Attendance window end, e.g. 20240214-19:00")
    parser.add_argument("--batch-names", default="", help="Comma-separated experiment names with existing evaluation metrics")
    parser.add_argument("--batch-output", default="", help="Output folder name under results/evaluations for batch summary")
    args = parser.parse_args()

    if args.batch_names:
        names = split_csv(args.batch_names)
        output_name = args.batch_output or ("batch-" + args.event)
        summary = summarize_batch(
            os.path.join("results", "evaluations"),
            names,
            os.path.join("results", "evaluations", output_name),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    checkpoints_dir = os.path.join("results", "checkpoints", args.name)
    compressed_dir = os.path.join("results", "compressed", args.name)
    evaluation_dir = os.path.join("results", "evaluations", args.name)
    os.makedirs(evaluation_dir, exist_ok=True)

    conversation = load_json(os.path.join(checkpoints_dir, "conversation.json"), {})
    movement = load_json(os.path.join(compressed_dir, "movement.json"), {})
    final_state = latest_checkpoint(checkpoints_dir)

    keywords = split_csv(args.keywords)
    rows = flatten_conversation(conversation)
    mentions = collect_mentions(rows, keywords)
    attendance = collect_attendance(movement, args.target_place, args.window_start, args.window_end)
    memory_summary = collect_memory_summary(final_state)
    event_board = build_event_board(args.event, mentions, attendance)
    reflection_candidates = build_reflection_candidates(event_board, mentions)
    goal_progress = build_goal_progress(event_board)

    metrics = {
        "experiment": args.name,
        "event": args.event,
        "keywords": keywords,
        "target_place": args.target_place,
        "window_start": args.window_start,
        "window_end": args.window_end,
        "checkpoint_count": len(checkpoint_files(checkpoints_dir)),
        "final_time": final_state.get("time"),
        "diffusion": {
            "mention_count": len(mentions),
            "known_agents": event_board["known_by"],
            "known_agent_count": len(event_board["known_by"]),
        },
        "commitments": {
            "accepted": event_board["accepted"],
            "accepted_count": len(event_board["accepted"]),
            "rejected": event_board["rejected"],
            "rejected_count": len(event_board["rejected"]),
        },
        "attendance": {
            "arrived": event_board["arrived"],
            "arrived_count": len(event_board["arrived"]),
        },
        "goal_progress": goal_progress,
        "memory_summary": memory_summary,
        "reflection_candidates": len(reflection_candidates),
    }

    outputs = {
        "metrics": os.path.join(evaluation_dir, "metrics.json"),
        "report": os.path.join(evaluation_dir, "report.md"),
        "event_board": os.path.join(evaluation_dir, "event_board.json"),
        "goal_progress": os.path.join(evaluation_dir, "goal_progress.json"),
        "reflection_candidates": os.path.join(evaluation_dir, "reflection_candidates.json"),
    }
    with open(outputs["metrics"], "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    with open(outputs["event_board"], "w", encoding="utf-8") as f:
        json.dump(event_board, f, ensure_ascii=False, indent=2)
    with open(outputs["goal_progress"], "w", encoding="utf-8") as f:
        json.dump(goal_progress, f, ensure_ascii=False, indent=2)
    with open(outputs["reflection_candidates"], "w", encoding="utf-8") as f:
        json.dump(reflection_candidates, f, ensure_ascii=False, indent=2)
    write_report(
        outputs["report"],
        metrics,
        mentions,
        attendance,
        event_board,
        reflection_candidates,
        goal_progress,
    )

    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
