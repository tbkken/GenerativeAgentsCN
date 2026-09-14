"""Action checkpoint round trips retain instants and simulation-local summaries."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from generative_agents.modules.memory import Action, Event
from generative_agents.runtime.context import SimulationClock


def _event():
    return Event("陈明远", "整理", "资料", address=["家", "住宅", "书房", "书桌"])


def test_restored_action_summary_uses_simulation_timezone_without_changing_instant():
    clock = SimulationClock(datetime(2026, 9, 9, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    snapshot = {
        "event": _event().to_dict(),
        "obj_event": None,
        "start": "20260909-07:27:00",
        "duration": 3,
    }
    original_snapshot = copy.deepcopy(snapshot)

    action = Action.from_dict(snapshot, clock=clock)

    assert snapshot == original_snapshot
    assert action.start == datetime(2026, 9, 8, 23, 27, tzinfo=timezone.utc)
    assert action.end == clock.get_date()
    assert action.end - action.start == timedelta(minutes=3)
    assert action.abstract()["status"] == "进行中 [20260909-07:27~20260909-07:30]"
    clock.advance(3)
    assert action.abstract()["status"] == "已完成 [20260909-07:27~20260909-07:30]"


@pytest.mark.parametrize(
    "zone_name",
    ["Asia/Shanghai", "Asia/Kolkata", "Pacific/Honolulu", "America/New_York", "UTC"],
)
def test_action_checkpoint_repeated_restores_preserve_cross_midnight_times(zone_name):
    zone = ZoneInfo(zone_name)
    start = datetime(2026, 9, 9, 23, 58, 12, 123456, tzinfo=zone)
    clock = SimulationClock(start)
    action = Action(_event(), start=start, duration=3, clock=clock)
    original_start = start.astimezone(timezone.utc)
    original_end = action.end.astimezone(timezone.utc)
    summary = "进行中 [20260909-23:58~20260910-00:01]"

    for _ in range(3):
        snapshot = json.loads(json.dumps(action.to_dict()))
        saved_start = datetime.fromisoformat(snapshot["start"])
        assert saved_start.utcoffset() is not None
        assert saved_start == original_start
        action = Action.from_dict(snapshot, clock=clock)
        assert action.start == original_start
        assert action.end == original_end
        assert action.end - action.start == timedelta(minutes=3)
        assert action.abstract()["status"] == summary
        assert not action.finished()

    clock.advance(4)
    assert action.finished()
    assert action.abstract()["status"] == summary.replace("进行中", "已完成")


@pytest.mark.parametrize(
    ("zone_name", "expected"),
    [
        ("Asia/Shanghai", "20260910-07:58~20260910-08:01"),
        ("Pacific/Honolulu", "20260909-13:58~20260909-14:01"),
    ],
)
def test_action_summary_converts_utc_inputs_to_the_injected_clock_zone(zone_name, expected):
    start = datetime(2026, 9, 9, 23, 58, tzinfo=timezone.utc)
    clock = SimulationClock(start.astimezone(ZoneInfo(zone_name)))
    action = Action(_event(), start=start, duration=3, clock=clock)

    assert action.abstract()["status"] == f"进行中 [{expected}]"
    assert action.start == start
    assert action.end == start + timedelta(minutes=3)
    assert datetime.fromisoformat(action.to_dict()["start"]) == start


def test_explicit_aware_action_without_clock_keeps_its_own_timezone():
    start = datetime(2026, 9, 9, 7, 27, tzinfo=ZoneInfo("Asia/Kolkata"))
    action = Action(_event(), start=start)

    assert action.abstract()["status"] == "已完成 [20260909-07:27~20260909-07:27]"
    assert datetime.fromisoformat(action.to_dict()["start"]) == start
    assert datetime.fromisoformat(action.to_dict()["start"]).utcoffset() == start.utcoffset()
