"""Public context injected into every Skill call of one Agent iteration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID


@dataclass(frozen=True, slots=True)
class IterationContext:
    """Immutable common input shared by Brain and all child Skills.

    It intentionally contains only observable simulation facts. Mutable world
    access remains behind MCP tools, while natural-language Skill outputs stay in
    the runtime trace and are never mistaken for replay facts.
    """

    run_id: UUID
    attempt_id: UUID
    agent_key: str
    agent_name: str
    step_no: int
    total_steps: int
    now: datetime
    stride_minutes: int
    coord: tuple[int, int]
    address: tuple[str, ...]
    spatial_semantics: tuple[Mapping[str, Any], ...] = ()
    variables: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.step_no < 1:
            raise ValueError("IterationContext.step_no must be positive")
        if self.total_steps < self.step_no:
            raise ValueError("IterationContext.total_steps cannot precede step_no")
        if self.stride_minutes < 1:
            raise ValueError("IterationContext.stride_minutes must be positive")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("IterationContext.now must be timezone-aware")

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "attempt_id": str(self.attempt_id),
            "agent": {
                "agent_key": self.agent_key,
                "name": self.agent_name,
                "coord": list(self.coord),
                "address": list(self.address),
            },
            "step": {
                "number": self.step_no,
                "total": self.total_steps,
                "stride_minutes": self.stride_minutes,
            },
            "now": self.now.isoformat(),
            "spatial_semantics": [dict(item) for item in self.spatial_semantics],
            "variables": dict(self.variables),
        }


__all__ = ["IterationContext"]
