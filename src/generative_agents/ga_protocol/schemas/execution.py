"""Compact execution evidence, separate from committed world facts and payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One boundary of an invocation; success never implies a world commit."""

    run_id: str
    attempt_id: str
    span_id: str
    parent_span_id: str | None
    kind: Literal["step", "actor", "skill", "script", "model", "interface"]
    phase: Literal["START", "END", "ROLLBACK"]
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    timestamp: datetime
    name: str
    step_no: int | None = None
    agent_key: str | None = None
    actor_name: str | None = None
    actor_kind: Literal["agent", "object"] | None = None
    skill_key: str | None = None
    call_id: str | None = None
    tool_call_id: str | None = None
    actor_index: int | None = None
    actor_total: int | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"step", "actor", "skill", "script", "model", "interface"}:
            raise ValueError("unknown execution kind")
        if self.phase not in {"START", "END", "ROLLBACK"}:
            raise ValueError("unknown execution phase")
        if self.status not in {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("unknown execution status")
        if self.actor_kind not in {None, "agent", "object"}:
            raise ValueError("unknown execution actor kind")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("execution timestamps must be timezone-aware")
        if not self.run_id or not self.attempt_id or not self.span_id or not self.name:
            raise ValueError("execution identities and name must not be empty")
        if self.parent_span_id == self.span_id:
            raise ValueError("execution span cannot be its own parent")
        if self.phase == "START" and self.status != "RUNNING":
            raise ValueError("execution START must be RUNNING")
        if self.phase != "START" and self.status == "RUNNING":
            raise ValueError("execution terminal boundaries cannot be RUNNING")
        if self.step_no is not None and self.step_no < 1:
            raise ValueError("execution step must be positive")
        if self.actor_index is not None and (
            self.actor_total is None or not 1 <= self.actor_index <= self.actor_total
        ):
            raise ValueError("execution actor index must be within its total")

    def as_dict(self) -> dict:
        record = asdict(self)
        record["timestamp"] = self.timestamp.isoformat()
        return record
