"""Calculate the book's synthetic example; never reads or changes a Run."""
from pathlib import Path
import json
from statistics import median


def summarize(data):
    if data.get("document_kind") != "synthetic-teaching-comparison":
        raise ValueError("Only the synthetic teaching format is supported.")
    rows = data["rows"]
    if len({row["teaching_row"] for row in rows}) != len(rows):
        raise ValueError("Duplicate teaching row.")
    groups = {}
    for row in rows:
        times = row["completed_minutes"]
        if not 0 <= len(times) <= row["tasks"] or row["tasks"] <= 0:
            raise ValueError("Invalid task count.")
        if any(not 0 <= t <= data["window_minutes"] for t in times):
            raise ValueError("Completion time outside the observation window.")
        if not 0 <= row["logical_calls"] <= row["physical_attempts"]:
            raise ValueError("Invalid call counts.")
        groups.setdefault(row["group"], []).append(row)
    result = {}
    for name, members in sorted(groups.items()):
        times = [t for row in members for t in row["completed_minutes"]]
        completed = len(times)
        tasks = sum(row["tasks"] for row in members)
        logical = sum(row["logical_calls"] for row in members)
        physical = sum(row["physical_attempts"] for row in members)
        result[name] = {
            "teaching_rows": len(members),
            "completed": completed,
            "tasks": tasks,
            "completion_rate": completed / tasks,
            "row_rates": [len(row["completed_minutes"]) / row["tasks"] for row in members],
            "unfinished_at_cutoff": tasks - completed,
            "completed_only_median_minutes": median(times) if times else None,
            "logical_calls_total": logical,
            "physical_attempts_total": physical,
            "logical_calls_per_success": logical / completed if completed else None,
            "physical_attempts_per_success": physical / completed if completed else None,
        }
    return {"provenance": data["provenance"], "groups": result}


if __name__ == "__main__":
    source = Path(__file__).with_name("comparison.json")
    result = summarize(json.loads(source.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
