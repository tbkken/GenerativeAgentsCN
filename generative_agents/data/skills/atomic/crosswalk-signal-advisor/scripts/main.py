"""Deterministic, instance-scoped pedestrian signal advice."""

from __future__ import annotations

from typing import Any


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _phase(context: dict[str, Any]) -> tuple[str, int]:
    state = dict(context.get("object_state") or {})
    explicit = str(state.get("pedestrian_signal") or "").strip().upper()
    if explicit in {"RED", "GREEN", "FLASHING"}:
        return explicit, 1

    cycle = dict(state.get("signal_cycle") or {})
    durations = (
        ("RED", _positive_int(cycle.get("red_steps"), 4)),
        ("GREEN", _positive_int(cycle.get("green_steps"), 5)),
        ("FLASHING", _positive_int(cycle.get("flashing_steps"), 2)),
    )
    offset = int(cycle.get("offset_steps") or 0)
    step_no = _positive_int(context.get("step_no"), 1)
    position = (step_no - 1 + offset) % sum(length for _, length in durations)
    for phase, length in durations:
        if position < length:
            return phase, length - position
        position -= length
    return "RED", 1


def run(input_text: str, context: dict[str, Any]) -> str:
    del input_text
    state = dict(context.get("object_state") or {})
    crossing = str(state.get("crossing_name") or "当前人行横道")
    phase, remaining = _phase(context)
    if phase == "RED":
        return (
            f"{crossing}当前为行人红灯，预计还剩 {remaining} 个世界步。"
            "车辆仍可能通行，请留在等候区，不要进入斑马线。"
        )
    if phase == "GREEN":
        return (
            f"{crossing}当前为行人绿灯，预计还剩 {remaining} 个世界步。"
            "确认车辆已经停止后，可以进入斑马线并连续通过。"
        )
    return (
        f"{crossing}当前为绿灯闪烁清空期，预计还剩 {remaining} 个世界步。"
        "已经在斑马线内应继续尽快通过；仍在等候区则不要再进入。"
    )
