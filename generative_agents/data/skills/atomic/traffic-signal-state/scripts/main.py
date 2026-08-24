"""Deterministic passive response for the pedestrian-signal demo."""

from __future__ import annotations


def _phase(context: dict) -> str:
    state = dict(context.get("object_state") or {})
    explicit = str(state.get("pedestrian_signal") or "").strip().upper()
    if explicit in {"RED", "GREEN"}:
        return explicit

    cycle = dict(state.get("signal_cycle") or {})
    red_steps = max(1, int(cycle.get("red_steps", 1)))
    green_steps = max(1, int(cycle.get("green_steps", 2)))
    offset = int(cycle.get("offset_steps", 0))
    step_no = max(1, int(context.get("step_no", 1)))
    position = (step_no - 1 + offset) % (red_steps + green_steps)
    return "RED" if position < red_steps else "GREEN"


def run(input_text: str, context: dict) -> str:
    del input_text
    if _phase(context) == "RED":
        return "当前为行人红灯，车辆仍在通行，请在路边等待，不要进入斑马线。"
    return "当前为行人绿灯，车辆已经停止，可以确认安全后通过斑马线。"
