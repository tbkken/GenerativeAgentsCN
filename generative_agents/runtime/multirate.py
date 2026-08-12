"""Deterministic virtual-time scheduler for capability-composed simulations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


Trigger = Literal["FIXED_INTERVAL", "EVENT", "STATE_CHANGE", "DECISION", "MANUAL"]


@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    task_key: str
    virtual_time_ms: int
    tick_no: int
    trigger: Trigger
    events: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True)
class ScheduledCapabilityTask:
    task_key: str
    trigger: Trigger
    callback: Callable[[CapabilityInvocation], None]
    interval_ms: int | None = None
    event_types: tuple[str, ...] = ()
    priority: int = 100
    start_offset_ms: int = 0
    enabled: bool = True
    next_due_ms: int = field(init=False)

    def __post_init__(self) -> None:
        self.next_due_ms = self.start_offset_ms
        if self.trigger in {"FIXED_INTERVAL", "DECISION"}:
            if self.interval_ms is None or self.interval_ms <= 0:
                raise ValueError(f"{self.trigger} task requires a positive interval_ms")
        elif self.interval_ms is not None:
            raise ValueError("only interval-driven tasks may declare interval_ms")
        if self.trigger == "EVENT" and not self.event_types:
            raise ValueError("EVENT task requires at least one event type")


@dataclass(frozen=True, slots=True)
class SchedulerRunSummary:
    elapsed_ms: int
    ticks: int
    invocation_counts: dict[str, int]
    trace: tuple[tuple[int, str, str], ...]


class MultiRateCapabilityScheduler:
    """Run fast dynamics and sparse cognition on one reproducible time grid."""

    def __init__(self, *, base_tick_ms: int = 100) -> None:
        if base_tick_ms < 1:
            raise ValueError("base_tick_ms must be positive")
        self.base_tick_ms = base_tick_ms
        self.virtual_time_ms = 0
        self.tick_no = 0
        self._tasks: dict[str, ScheduledCapabilityTask] = {}
        self._event_queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._state_changes: deque[dict[str, Any]] = deque()
        self._counts: dict[str, int] = defaultdict(int)
        self._trace: list[tuple[int, str, str]] = []

    def register(self, task: ScheduledCapabilityTask) -> None:
        if task.task_key in self._tasks:
            raise ValueError(f"duplicate task key: {task.task_key}")
        if task.interval_ms and task.interval_ms % self.base_tick_ms:
            raise ValueError(
                f"task {task.task_key} interval must be a multiple of base_tick_ms"
            )
        if task.start_offset_ms % self.base_tick_ms:
            raise ValueError(
                f"task {task.task_key} offset must be a multiple of base_tick_ms"
            )
        self._tasks[task.task_key] = task

    def publish_event(
        self, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        self._event_queues[event_type].append(
            {
                "event_type": event_type,
                "payload": dict(payload or {}),
                "virtual_time_ms": self.virtual_time_ms,
            }
        )

    def publish_state_change(
        self, state_key: str, payload: dict[str, Any] | None = None
    ) -> None:
        self._state_changes.append(
            {
                "event_type": "event/state_changed",
                "state_key": state_key,
                "payload": dict(payload or {}),
                "virtual_time_ms": self.virtual_time_ms,
            }
        )

    def restore_clock(self, virtual_time_ms: int, tick_no: int | None = None) -> None:
        """Resume at an already committed boundary without replaying old ticks."""

        if virtual_time_ms < 0 or virtual_time_ms % self.base_tick_ms:
            raise ValueError("virtual_time_ms must align to base_tick_ms")
        expected_tick = virtual_time_ms // self.base_tick_ms
        if tick_no is not None and tick_no != expected_tick:
            raise ValueError("tick_no does not match virtual_time_ms")
        self.virtual_time_ms = virtual_time_ms
        self.tick_no = expected_tick
        for task in self._tasks.values():
            if task.interval_ms is None:
                continue
            if virtual_time_ms <= task.start_offset_ms:
                task.next_due_ms = task.start_offset_ms
                continue
            elapsed = virtual_time_ms - task.start_offset_ms
            periods = (elapsed + task.interval_ms - 1) // task.interval_ms
            task.next_due_ms = task.start_offset_ms + periods * task.interval_ms

    def invoke_manual(self, task_key: str) -> None:
        task = self._tasks[task_key]
        if task.trigger != "MANUAL":
            raise ValueError("only MANUAL tasks may be invoked manually")
        self._invoke(task, ())

    def run(self, duration_ms: int) -> SchedulerRunSummary:
        if duration_ms < 0 or duration_ms % self.base_tick_ms:
            raise ValueError("duration_ms must be a non-negative multiple of base_tick_ms")
        target = self.virtual_time_ms + duration_ms
        while self.virtual_time_ms < target:
            self._run_tick()
            self.virtual_time_ms += self.base_tick_ms
            self.tick_no += 1
        return SchedulerRunSummary(
            elapsed_ms=self.virtual_time_ms,
            ticks=self.tick_no,
            invocation_counts=dict(self._counts),
            trace=tuple(self._trace),
        )

    def _run_tick(self) -> None:
        interval_tasks = [
            task
            for task in self._tasks.values()
            if task.enabled
            and task.trigger in {"FIXED_INTERVAL", "DECISION"}
            and task.next_due_ms <= self.virtual_time_ms
        ]
        for task in sorted(interval_tasks, key=lambda item: (item.priority, item.task_key)):
            self._invoke(task, ())
            task.next_due_ms += task.interval_ms or self.base_tick_ms

        # Events are broadcast facts.  Snapshot the queues so multiple
        # subscribers receive the same event instead of the first task
        # destructively consuming it for every other subscriber.
        event_snapshot = {
            event_type: tuple(queue)
            for event_type, queue in self._event_queues.items()
            if queue
        }
        event_tasks = [
            task
            for task in self._tasks.values()
            if task.enabled
            and task.trigger == "EVENT"
            and any(event_snapshot.get(event_type) for event_type in task.event_types)
        ]
        for task in sorted(event_tasks, key=lambda item: (item.priority, item.task_key)):
            events: list[dict[str, Any]] = []
            for event_type in task.event_types:
                events.extend(event_snapshot.get(event_type, ()))
            self._invoke(task, tuple(events))
        for event_type in event_snapshot:
            self._event_queues[event_type].clear()

        changes = tuple(self._state_changes)
        if changes:
            state_tasks = [
                task
                for task in self._tasks.values()
                if task.enabled and task.trigger == "STATE_CHANGE"
            ]
            for task in sorted(
                state_tasks, key=lambda item: (item.priority, item.task_key)
            ):
                self._invoke(task, changes)
            self._state_changes.clear()

    def _invoke(
        self,
        task: ScheduledCapabilityTask,
        events: tuple[dict[str, Any], ...],
    ) -> None:
        invocation = CapabilityInvocation(
            task_key=task.task_key,
            virtual_time_ms=self.virtual_time_ms,
            tick_no=self.tick_no,
            trigger=task.trigger,
            events=events,
        )
        task.callback(invocation)
        self._counts[task.task_key] += 1
        self._trace.append((self.virtual_time_ms, task.task_key, task.trigger))


__all__ = [
    "CapabilityInvocation",
    "MultiRateCapabilityScheduler",
    "ScheduledCapabilityTask",
    "SchedulerRunSummary",
]
