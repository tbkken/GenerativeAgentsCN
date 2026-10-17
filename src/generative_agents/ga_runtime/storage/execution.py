"""Append-only invocation boundaries without model or interface payload copies."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from generative_agents.ga_protocol.schemas.execution import ExecutionEvent
from generative_agents.ga_runtime.models.retry import ModelCallCancelled


class ExecutionTraceWriter:
    """One Attempt's writer and execution-local parent scope."""

    def __init__(self, paths, *, run_id, attempt_id, attempt_no):
        if paths.run_id != run_id or attempt_no < 1:
            raise ValueError("execution writer ownership mismatch")
        paths.ensure()
        self.path = paths.traces / f"execution-{attempt_no:03d}.jsonl"
        self.run_id, self.attempt_id = str(run_id), str(attempt_id)
        self._current = ContextVar(f"execution-span-{attempt_id}", default=None)
        self._lock = threading.Lock()
        self._models: dict[str, ExecutionEvent] = {}
        self._sequence = 0
        if self.path.exists():
            content = self.path.read_bytes()
            if content and not content.endswith(b"\n"):
                raise ValueError("execution trace ends with an incomplete record")
            for line in content.splitlines():
                record = json.loads(line)
                if (record.get("run_id") != self.run_id or
                        record.get("attempt_id") != self.attempt_id or
                        record.get("event_seq") != self._sequence + 1):
                    raise ValueError("execution trace identity or sequence mismatch")
                self._sequence += 1

    def append(self, event: ExecutionEvent) -> None:
        if event.run_id != self.run_id or event.attempt_id != self.attempt_id:
            raise ValueError("execution event belongs to another Attempt")
        with self._lock:
            record = {**event.as_dict(), "schema_version": 1, "event_seq": self._sequence + 1}
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence += 1

    def start(self, *, kind, name, **fields) -> ExecutionEvent:
        parent = self._current.get()
        inherited = {key: getattr(parent, key) for key in (
            "step_no", "agent_key", "actor_name", "actor_kind", "skill_key",
            "actor_index", "actor_total",
        )} if parent else {}
        inherited.update(fields)
        event = ExecutionEvent(
            run_id=self.run_id, attempt_id=self.attempt_id,
            span_id=inherited.pop("span_id", str(uuid4())),
            parent_span_id=parent.span_id if parent else None,
            kind=kind, name=name, phase="START", status="RUNNING",
            timestamp=datetime.now(UTC), **inherited,
        )
        self.append(event)
        return event

    def finish(self, event, *, status="SUCCEEDED", error_code=None) -> None:
        self.append(replace(event, phase="END", status=status,
                            timestamp=datetime.now(UTC), error_code=error_code))

    def rollback(self, *, error_code=None, cancelled=False) -> None:
        event = self._current.get()
        if event is not None:
            self.append(replace(event, phase="ROLLBACK", timestamp=datetime.now(UTC),
                                status="CANCELLED" if cancelled else "FAILED",
                                error_code=error_code))

    def observe_model(self, event) -> None:
        """Join logical model calls to their actual active Skill, without payloads."""
        call_id = str(event.call_id)
        if str(event.event_type) == "PHYSICAL_START" and call_id not in self._models:
            self._models[call_id] = self.start(
                kind="model", name=event.resolved_model, span_id=call_id,
                call_id=call_id, step_no=event.step_no, agent_key=event.agent_key,
                skill_key=event.prompt_key,
            )
        elif str(event.event_type) == "LOGICAL_END":
            start = self._models.pop(call_id, None)
            if start is None:
                start = self.start(kind="model", name=event.resolved_model,
                                   span_id=call_id, call_id=call_id,
                                   step_no=event.step_no, agent_key=event.agent_key,
                                   skill_key=event.prompt_key)
            self.finish(start, status={"ABORTED": "CANCELLED", "FALLBACK": "FAILED"}.get(
                str(event.status), str(event.status)), error_code=event.error_code)


class ExecutionSpan:
    """Allows a returned interface rejection to close as FAILED without raising."""

    status = "SUCCEEDED"
    error_code = None


@contextmanager
def execution_span(writer, *, kind, name, **fields):
    span = ExecutionSpan()
    if writer is None:
        yield span
        return
    start = writer.start(kind=kind, name=name, **fields)
    token = writer._current.set(start)
    try:
        yield span
    except BaseException as exc:
        writer.finish(start, status="CANCELLED" if isinstance(exc, ModelCallCancelled) else "FAILED",
                      error_code=type(exc).__name__)
        raise
    else:
        writer.finish(start, status=span.status, error_code=span.error_code)
    finally:
        writer._current.reset(token)
