"""Retry waits that remain responsive to per-Run pause and cancel controls."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class RetryControl(Protocol):
    @property
    def pause_requested(self) -> bool: ...

    @property
    def cancel_requested(self) -> bool: ...


def interruptible_wait(
    seconds: float,
    *,
    control: RetryControl | None = None,
    sleep: Callable[[float], None] = time.sleep,
    quantum_seconds: float = 0.1,
) -> bool:
    """Wait for a retry without hiding a Run control request.

    Returns ``False`` as soon as pause/cancel is requested. The injected sleep
    function keeps the behavior deterministic and fast in unit tests.
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
        control is not None
        and (control.pause_requested or control.cancel_requested)
    )
