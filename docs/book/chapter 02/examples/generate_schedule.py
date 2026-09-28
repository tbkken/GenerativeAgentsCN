"""An offline deterministic baseline, not an LLM-generated result."""
from __future__ import annotations

from datetime import datetime, timedelta

from common import ROOT, read_csv, write_json
from validate_schedule import DATE, ZONE, get_room_availability, overlaps, validate_schedule


def generate_schedule() -> dict:
    schedule: dict = {"date": DATE, "timezone": ZONE, "activities": []}
    for request in read_csv("requests.csv"):
        chosen = None
        for room_id in request["allowed_room_ids"].split("|"):
            availability = get_room_availability(room_id, DATE)
            if int(request["participants"]) > availability["capacity"]:
                continue
            start = datetime.fromisoformat(request["earliest_start"])
            duration = timedelta(minutes=int(request["duration_minutes"]))
            occupied = availability["bookings"] + [item for item in schedule["activities"] if item["room_id"] == room_id]
            while start + duration <= datetime.fromisoformat(request["latest_end"]):
                end = start + duration
                if not any(overlaps(start, end, datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"])) for item in occupied):
                    chosen = {"activity_id": request["activity_id"], "room_id": room_id, "start": start.isoformat(), "end": end.isoformat(), "participants": int(request["participants"])}
                    break
                start += timedelta(minutes=1)
            if chosen:
                break
        if not chosen:
            raise RuntimeError(f"贪心基线未找到{request['activity_id']}的安排；不代表不存在整体解。")
        schedule["activities"].append(chosen)
    report = validate_schedule(schedule)
    if not report["valid"]:
        raise RuntimeError(str(report))
    return schedule


if __name__ == "__main__":
    schedule = generate_schedule()
    write_json(ROOT / "output" / "baseline-schedule.json", schedule)
    print("已生成确定性基线output/baseline-schedule.json（未调用模型）。")
