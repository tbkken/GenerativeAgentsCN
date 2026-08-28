"""OpenAPI contracts for windowed Replay V2 transport."""

from __future__ import annotations

from typing import Any, Literal

from generative_agents.config.schema import StrictModel


class ReplayManifestResponse(StrictModel):
    """回放器启动所需的 Run、地图、智能体和可用步骤清单。"""

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
    execution_mode: Literal["SKILL_BRAIN"]
    brain_skill: str
    step_interval_ms: int | None = None
    start_time: str
    agents: list[dict[str, Any]]
    partial: bool


class ReplayStepsResponse(StrictModel):
    """按步骤窗口分页返回的 Replay V2 帧集合。"""

    run_id: str
    source_step: int
    available_step: int
    result_version: int
    from_step: int
    next_from_step: int | None = None
    partial: bool
    world_state_before: dict[str, dict[str, Any]]
    steps: list[dict[str, Any]]
