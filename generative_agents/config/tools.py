"""Tool entities and Agent-owned capability extension contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .capabilities import StableKey
from .schema import StrictModel
from .spatial_assets import SpatialAppearance, SpatialCapabilityAttachment


ToolKind = Literal[
    "CAR",
    "BICYCLE",
    "MOTORCYCLE",
    "ACCESS_CARD",
    "DEVICE",
    "OTHER",
]


class ToolMobility(StrictModel):
    mode: Literal["NONE", "ROAD", "BICYCLE_NETWORK", "PEDESTRIAN_NETWORK"] = "NONE"
    max_speed_mps: float = Field(default=0, ge=0, le=200)
    max_acceleration_mps2: float = Field(default=0, ge=0, le=100)
    max_deceleration_mps2: float = Field(default=0, ge=0, le=100)
    operator_required: bool = False
    capacity: int = Field(default=1, ge=1, le=500)

    @model_validator(mode="after")
    def validate_motion(self) -> "ToolMobility":
        if self.mode == "NONE" and any(
            value > 0
            for value in (
                self.max_speed_mps,
                self.max_acceleration_mps2,
                self.max_deceleration_mps2,
            )
        ):
            raise ValueError("non-mobile tools cannot declare motion limits")
        if self.mode != "NONE" and self.max_speed_mps <= 0:
            raise ValueError("mobile tools require a positive max_speed_mps")
        return self


class ToolContract(StrictModel):
    schema_version: Literal["ga-tool/v1"] = "ga-tool/v1"
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    summary: Annotated[str, StringConstraints(max_length=2_000)] = ""
    kind: ToolKind
    appearance: SpatialAppearance
    mobility: ToolMobility = Field(default_factory=ToolMobility)
    tags: list[StableKey] = Field(default_factory=list, max_length=100)
    interfaces: list[StableKey] = Field(default_factory=list, max_length=100)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    capability_attachments: list[SpatialCapabilityAttachment] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_relations(self) -> "ToolContract":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tool tags must be unique")
        if len(self.interfaces) != len(set(self.interfaces)):
            raise ValueError("tool interfaces must be unique")
        keys = [item.attachment_key for item in self.capability_attachments]
        if len(keys) != len(set(keys)):
            raise ValueError("tool capability attachment keys must be unique")
        vehicle_kinds = {"CAR", "BICYCLE", "MOTORCYCLE"}
        if self.kind in vehicle_kinds and self.mobility.mode == "NONE":
            raise ValueError("vehicle tools must declare a mobility mode")
        if self.kind not in vehicle_kinds and self.mobility.mode != "NONE":
            raise ValueError("only vehicle tools may declare mobility")
        return self


class AgentToolGrant(StrictModel):
    grant_key: StableKey
    tool_revision_id: str = Field(min_length=1, max_length=36)
    quantity: int = Field(default=1, ge=1, le=10_000)
    relation: Literal["OWNS", "CARRIES", "MAY_USE"] = "OWNS"
    initial_location_ref: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ] = "agent:self"
    available: bool = True
    state_overrides: dict[str, Any] = Field(default_factory=dict)


class MobilityChoicePolicy(StrictModel):
    enabled: bool = False
    default_mode: Literal["WALK", "FASTEST_AVAILABLE", "LOWEST_RISK"] = "WALK"
    decision_capability_revision_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    decision_bundle_revision_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    urgency_threshold_minutes: int = Field(default=15, ge=0, le=1_440)
    decision_interval_ms: int = Field(default=60_000, ge=100, le=86_400_000)

    @model_validator(mode="after")
    def validate_decision_reference(self) -> "MobilityChoicePolicy":
        has_capability = bool(self.decision_capability_revision_id)
        has_bundle = bool(self.decision_bundle_revision_id)
        if self.enabled and has_capability == has_bundle:
            raise ValueError(
                "enabled mobility choice requires exactly one decision capability or bundle"
            )
        if not self.enabled and (has_capability or has_bundle):
            raise ValueError("disabled mobility choice cannot reference a decision policy")
        return self


class AgentCapabilityExtension(StrictModel):
    schema_version: Literal["ga-agent-extension/v1"] = "ga-agent-extension/v1"
    capability_bundle_revision_ids: list[str] = Field(default_factory=list, max_length=100)
    tool_grants: list[AgentToolGrant] = Field(default_factory=list, max_length=1_000)
    mobility_choice: MobilityChoicePolicy = Field(default_factory=MobilityChoicePolicy)
    reasoning_interval_ms: int = Field(default=60_000, ge=100, le=86_400_000)

    @field_validator("capability_bundle_revision_ids")
    @classmethod
    def unique_capability_bundles(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Agent capability bundle revisions must be unique")
        return value

    @model_validator(mode="after")
    def validate_grants(self) -> "AgentCapabilityExtension":
        keys = [item.grant_key for item in self.tool_grants]
        if len(keys) != len(set(keys)):
            raise ValueError("Agent tool grant keys must be unique")
        return self


__all__ = [
    "AgentCapabilityExtension",
    "AgentToolGrant",
    "MobilityChoicePolicy",
    "ToolContract",
    "ToolMobility",
]
