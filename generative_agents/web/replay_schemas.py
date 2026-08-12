"""OpenAPI contracts for windowed Replay V2 transport."""

from __future__ import annotations

from typing import Any, Literal

from generative_agents.config.schema import StrictModel


class ReplayManifestResponse(StrictModel):
    schema_version: Literal[2]
    generator_version: str
    source_kind: str
    run_id: str
    revision_id: str
    definition_hash: str
    world: dict[str, Any]
    source_step: int
    available_step: int
    stride_minutes: int
    execution_mode: Literal["LEGACY_TOWN", "CAPABILITY_COMPOSED"]
    step_interval_ms: int | None = None
    start_time: str
    agents: list[dict[str, Any]]
    partial: bool


class ReplayStepsResponse(StrictModel):
    run_id: str
    source_step: int
    available_step: int
    result_version: int
    from_step: int
    next_from_step: int | None = None
    partial: bool
    steps: list[dict[str, Any]]
