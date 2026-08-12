"""Experiment-level composition for second-granularity capability simulations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .capabilities import PortKey, StableKey
from .schema import StrictModel


ChannelRef = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=240),
]


class MultiRateClock(StrictModel):
    base_tick_ms: int = Field(default=100, ge=10, le=60_000)
    duration_ms: int = Field(default=60_000, ge=100, le=86_400_000)
    snapshot_interval_ms: int = Field(default=200, ge=10, le=86_400_000)

    @model_validator(mode="after")
    def validate_clock_grid(self) -> "MultiRateClock":
        if self.duration_ms % self.base_tick_ms:
            raise ValueError("duration_ms must be a multiple of base_tick_ms")
        if self.snapshot_interval_ms % self.base_tick_ms:
            raise ValueError("snapshot_interval_ms must be a multiple of base_tick_ms")
        return self


class ScenePose(StrictModel):
    x_m: float
    y_m: float
    heading_degrees: float = Field(default=0, ge=-360, le=360)


class ScenarioActorBinding(StrictModel):
    actor_key: StableKey
    experiment_agent_key: StableKey
    agent_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    role: Literal["DRIVER", "PEDESTRIAN", "CYCLIST", "OBSERVER", "OTHER"]
    initial_pose: ScenePose
    route: list[ScenePose] = Field(default_factory=list, max_length=10_000)
    radius_m: float = Field(default=0.35, gt=0, le=20)
    reasoning_interval_ms: int = Field(default=60_000, ge=100, le=86_400_000)
    active_tool_instance_key: StableKey | None = None


class ScenarioToolInstance(StrictModel):
    instance_key: StableKey
    tool_revision_id: str = Field(min_length=1, max_length=36)
    owner_actor_key: StableKey
    operator_actor_key: StableKey | None = None
    initial_pose: ScenePose
    route: list[ScenePose] = Field(default_factory=list, max_length=10_000)
    radius_m: float = Field(default=1.0, gt=0, le=50)
    state_overrides: dict[str, Any] = Field(default_factory=dict)


class ScenarioCapabilityMount(StrictModel):
    mount_key: StableKey
    capability_bundle_revision_id: str = Field(min_length=1, max_length=36)
    target_bindings: dict[StableKey, ChannelRef] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_bindings: dict[PortKey, ChannelRef] = Field(default_factory=dict)
    output_bindings: dict[PortKey, ChannelRef] = Field(default_factory=dict)
    enabled: bool = True


class ScenarioMetric(StrictModel):
    metric_key: StableKey
    kind: Literal[
        "MINIMUM_DISTANCE",
        "TIME_TO_COLLISION",
        "COLLISION",
        "PEDESTRIAN_WAIT",
        "VEHICLE_YIELD",
        "CUSTOM",
    ]
    source_channel: ChannelRef | None = None
    unit: Annotated[str, StringConstraints(max_length=32)] = ""
    collision_threshold_m: float = Field(default=0.5, ge=0, le=100)


class ScenarioStopCondition(StrictModel):
    condition_key: StableKey
    metric_key: StableKey
    operator: Literal["LT", "LTE", "EQ", "GTE", "GT"]
    threshold: float


class ExperimentCapabilityExtension(StrictModel):
    schema_version: Literal["ga-experiment-capability/v1"] = (
        "ga-experiment-capability/v1"
    )
    mode: Literal["LEGACY_TOWN", "CAPABILITY_COMPOSED"] = "LEGACY_TOWN"
    map_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    clock: MultiRateClock = Field(default_factory=MultiRateClock)
    actors: list[ScenarioActorBinding] = Field(default_factory=list, max_length=1_000)
    tool_instances: list[ScenarioToolInstance] = Field(
        default_factory=list, max_length=5_000
    )
    capability_mounts: list[ScenarioCapabilityMount] = Field(
        default_factory=list, max_length=1_000
    )
    metrics: list[ScenarioMetric] = Field(default_factory=list, max_length=500)
    stop_conditions: list[ScenarioStopCondition] = Field(
        default_factory=list, max_length=100
    )

    @field_validator("actors")
    @classmethod
    def unique_actors(
        cls, value: list[ScenarioActorBinding]
    ) -> list[ScenarioActorBinding]:
        keys = [item.actor_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("scenario actor keys must be unique")
        return value

    @model_validator(mode="after")
    def validate_assembly(self) -> "ExperimentCapabilityExtension":
        tool_keys = [item.instance_key for item in self.tool_instances]
        mount_keys = [item.mount_key for item in self.capability_mounts]
        metric_keys = [item.metric_key for item in self.metrics]
        if len(tool_keys) != len(set(tool_keys)):
            raise ValueError("scenario tool instance keys must be unique")
        if len(mount_keys) != len(set(mount_keys)):
            raise ValueError("scenario capability mount keys must be unique")
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("scenario metric keys must be unique")
        if self.mode == "LEGACY_TOWN":
            if any(
                (
                    self.map_revision_id,
                    self.actors,
                    self.tool_instances,
                    self.capability_mounts,
                    self.metrics,
                    self.stop_conditions,
                )
            ):
                raise ValueError("LEGACY_TOWN extension cannot contain composed scene data")
            return self
        if self.map_revision_id is None:
            raise ValueError("CAPABILITY_COMPOSED scenarios require map_revision_id")
        if not self.actors:
            raise ValueError("CAPABILITY_COMPOSED scenarios require at least one actor")
        actor_keys = {item.actor_key for item in self.actors}
        tool_key_set = set(tool_keys)
        for tool in self.tool_instances:
            if tool.owner_actor_key not in actor_keys:
                raise ValueError(f"tool {tool.instance_key} references unknown owner actor")
            if tool.operator_actor_key and tool.operator_actor_key not in actor_keys:
                raise ValueError(
                    f"tool {tool.instance_key} references unknown operator actor"
                )
        for actor in self.actors:
            if (
                actor.active_tool_instance_key
                and actor.active_tool_instance_key not in tool_key_set
            ):
                raise ValueError(
                    f"actor {actor.actor_key} references unknown active tool instance"
                )
            if actor.reasoning_interval_ms % self.clock.base_tick_ms:
                raise ValueError(
                    f"actor {actor.actor_key} reasoning interval must align to base tick"
                )
        for condition in self.stop_conditions:
            if condition.metric_key not in set(metric_keys):
                raise ValueError(
                    f"stop condition {condition.condition_key} references unknown metric"
                )
        return self


__all__ = [
    "ExperimentCapabilityExtension",
    "MultiRateClock",
    "ScenarioActorBinding",
    "ScenarioCapabilityMount",
    "ScenarioMetric",
    "ScenarioStopCondition",
    "ScenarioToolInstance",
]
