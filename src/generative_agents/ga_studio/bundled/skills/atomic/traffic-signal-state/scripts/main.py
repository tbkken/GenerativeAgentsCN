"""Deterministic passive response for the pedestrian-signal demo."""

from __future__ import annotations


def _phase(context: dict) -> str:
    """执行`phase`的内部处理，供当前模块或类复用。

    参数:
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`dict`。

    返回:
        返回处理后的文本或稳定标识。
    """
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
    """执行当前组件负责的完整流程，并返回本次执行结果。

    参数:
        input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`dict`。

    返回:
        返回处理后的文本或稳定标识。
    """
    del input_text
    if _phase(context) == "RED":
        return "当前为行人红灯，车辆仍在通行，请在路边等待，不要进入斑马线。"
    return "当前为行人绿灯，车辆已经停止，可以确认安全后通过斑马线。"
