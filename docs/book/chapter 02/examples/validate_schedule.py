"""Deterministic business checks; requires only Python 3.11+ standard library."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
from typing import Any

from common import DATA, read_csv, read_json, write_json

DATE = "2026-10-17"
ZONE = "Asia/Shanghai"
OPEN = datetime.fromisoformat(f"{DATE}T09:00:00+08:00")
CLOSE = datetime.fromisoformat(f"{DATE}T12:00:00+08:00")
TIME_PATTERN = re.compile(r"^2026-10-17T\d{2}:\d{2}:00\+08:00$")
ROOT_KEYS = {"date", "timezone", "activities"}
ITEM_KEYS = {"activity_id", "room_id", "start", "end", "participants"}


def overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end


def get_room_availability(room_id: str, date: str) -> dict[str, Any]:
    rooms = {row["room_id"]: row for row in read_csv("rooms.csv")}
    if type(room_id) is not str or room_id not in rooms:
        raise ValueError("Unknown room_id")
    if type(date) is not str or date != DATE:
        raise ValueError(f"Only {DATE} is available in this teaching fixture")
    source = read_json(DATA / "availability.json")
    return {
        "room_id": room_id, "date": DATE, "timezone": ZONE,
        "capacity": int(rooms[room_id]["capacity"]),
        "opening": {"start": OPEN.isoformat(), "end": CLOSE.isoformat()},
        "bookings": [dict(item) for item in source["bookings"] if item["room_id"] == room_id],
        "source": "data/rooms.csv; data/availability.json; data/rules.md",
        "read_only": True,
    }


def validate_schedule(schedule: Any, data_dir: Path = DATA) -> dict[str, Any]:
    rooms = {row["room_id"]: row for row in read_csv("rooms.csv", data_dir)}
    requests = {row["activity_id"]: row for row in read_csv("requests.csv", data_dir)}
    bookings = read_json(data_dir / "availability.json")["bookings"]
    errors: list[dict[str, str]] = []

    def error(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    def report(count: int = 0) -> dict[str, Any]:
        return {"valid": not errors, "checked_activity_count": count, "errors": errors,
                "rule_set": "open-day-2026-10-17", "meaning": "仅检验本练习材料中的排期约束；未创建预约。"}

    def exact_keys(item: dict, expected: set[str], path: str) -> None:
        for key in sorted(expected - set(item)):
            error("missing_field", f"{path}.{key}", "缺少必填字段")
        for key in sorted(set(item) - expected, key=str):
            error("extra_field", f"{path}.{key}", "不接受未声明字段")

    def parse_time(value: Any, path: str) -> datetime | None:
        if type(value) is not str or not TIME_PATTERN.fullmatch(value):
            error("invalid_time", path, "须为活动当日、整分钟、带+08:00偏移的ISO时间")
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            error("invalid_time", path, "无效日期时间")
            return None

    if type(schedule) is not dict:
        error("invalid_type", "$", "排期根节点必须是对象")
        return report()
    exact_keys(schedule, ROOT_KEYS, "$")
    if schedule.get("date") != DATE:
        error("wrong_date", "$.date", f"日期必须为{DATE}")
    if schedule.get("timezone") != ZONE:
        error("wrong_timezone", "$.timezone", f"时区必须为{ZONE}")
    activities = schedule.get("activities")
    if type(activities) is not list:
        error("invalid_type", "$.activities", "activities必须为数组")
        return report()
    seen: set[str] = set()
    intervals: list[tuple[str, datetime, datetime, str]] = []
    for index, item in enumerate(activities):
        path = f"$.activities[{index}]"
        if type(item) is not dict:
            error("invalid_type", path, "活动必须为对象")
            continue
        exact_keys(item, ITEM_KEYS, path)
        activity_id = item.get("activity_id")
        room_id = item.get("room_id")
        known_activity = type(activity_id) is str and activity_id in requests
        known_room = type(room_id) is str and room_id in rooms
        if not known_activity:
            error("unknown_activity", f"{path}.activity_id", "未知或错误类型的活动ID")
        else:
            if activity_id in seen:
                error("duplicate_activity", f"{path}.activity_id", "每项活动只能出现一次")
            seen.add(activity_id)
        if not known_room:
            error("unknown_room", f"{path}.room_id", "未知或错误类型的房间ID")
        participants = item.get("participants")
        if type(participants) is not int or participants <= 0:
            error("invalid_participants", f"{path}.participants", "人数须为正整数；布尔值不算整数")
        else:
            if known_room and participants > int(rooms[room_id]["capacity"]):
                error("capacity_exceeded", f"{path}.participants", "人数超过房间容量")
            if known_activity and participants != int(requests[activity_id]["participants"]):
                error("participants_mismatch", f"{path}.participants", "人数与活动需求不一致")
        if known_activity and known_room and room_id not in requests[activity_id]["allowed_room_ids"].split("|"):
            error("room_not_allowed", f"{path}.room_id", "房间不在该活动的允许列表中")
        start = parse_time(item.get("start"), f"{path}.start")
        end = parse_time(item.get("end"), f"{path}.end")
        if start is None or end is None:
            continue
        if start >= end:
            error("invalid_interval", path, "开始时间必须早于结束时间")
            continue
        if start < OPEN or end > CLOSE:
            error("outside_opening", path, "活动超出开放时间")
        if known_activity:
            request = requests[activity_id]
            if end - start != timedelta(minutes=int(request["duration_minutes"])):
                error("duration_mismatch", path, "活动时长与需求不一致")
            if start < datetime.fromisoformat(request["earliest_start"]) or end > datetime.fromisoformat(request["latest_end"]):
                error("outside_window", path, "活动超出自身可用窗口")
        if known_room:
            for booking in bookings:
                if booking["room_id"] == room_id and overlaps(start, end, datetime.fromisoformat(booking["start"]), datetime.fromisoformat(booking["end"])):
                    error("booking_conflict", path, f"与已有预约{booking['booking_id']}冲突")
            for previous_room, previous_start, previous_end, previous_path in intervals:
                if previous_room == room_id and overlaps(start, end, previous_start, previous_end):
                    error("activity_conflict", path, f"与{previous_path}占用同一房间的时间重叠")
            intervals.append((room_id, start, end, path))
    for missing in sorted(set(requests) - seen):
        error("missing_activity", "$.activities", f"缺少活动{missing}")
    return report(len(activities))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schedule", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate_schedule(read_json(args.schedule))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.output.resolve() == args.schedule.resolve():
                raise ValueError("报告路径不能覆盖输入排期")
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"valid": False, "error": "input_or_io_error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
