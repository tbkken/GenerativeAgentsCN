"""Retry waits that remain responsive to per-Run pause and cancel controls."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class RetryControl(Protocol):
    @property
    def pause_requested(self) -> bool:
        """暂停`requested`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        ...

    @property
    def cancel_requested(self) -> bool:
        """取消`requested`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        ...


def interruptible_wait(
    seconds: float,
    *,
    control: RetryControl | None = None,
    sleep: Callable[[float], None] = time.sleep,
    quantum_seconds: float = 0.1,
) -> bool:
    """等待重试间隔，同时及时响应暂停或取消信号。

    参数:
        seconds: 超时、等待或租约计算使用的秒数。 类型：`float`。
        control: 运行控制器，用于在安全边界检测暂停、取消或终止请求。 类型：`RetryControl | None`。 默认值：`None`。
        sleep: 是否允许重试过程实际等待；测试可关闭等待。 类型：`Callable[[float], None]`。 默认值：`time.sleep`。
        quantum_seconds: `quantum`采用的秒数。 类型：`float`。 默认值：`0.1`。

    返回:
        条件成立时返回 `True`，否则返回 `False`。
    """

    remaining = max(0.0, float(seconds))
    quantum = max(0.01, float(quantum_seconds))
    while remaining > 0:
        if control is not None and (
            control.pause_requested or control.cancel_requested
        ):
            return False
        interval = min(remaining, quantum)
        sleep(interval)
        remaining -= interval
    return not (
        control is not None and (control.pause_requested or control.cancel_requested)
    )
