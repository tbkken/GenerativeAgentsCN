"""Versioned contracts for reusable map blocks, objects, zones and networks."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .capabilities import PortKey, StableKey
from .schema import StrictModel


SpatialAssetKind = Literal["TILE", "OBJECT", "ZONE", "MARKING", "NETWORK"]
AppearanceMode = Literal["COLOR", "EMOJI", "IMAGE", "SPRITE"]
AttachmentRef = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=240),
]


class SpatialAppearanceVariant(StrictModel):
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    emoji: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)] | None = None
    asset_path: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ] | None = None

    @model_validator(mode="after")
    def require_one_value(self) -> "SpatialAppearanceVariant":
        if sum(value is not None for value in (self.color, self.emoji, self.asset_path)) != 1:
            raise ValueError("appearance variant requires exactly one visual value")
        return self


class SpatialAppearance(StrictModel):
    mode: AppearanceMode
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    emoji: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)] | None = None
    asset_path: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ] | None = None
    scale: float = Field(default=1.0, gt=0, le=100)
    rotation_degrees: float = Field(default=0, ge=-360, le=360)
    state_variants: dict[StableKey, SpatialAppearanceVariant] = Field(
        default_factory=dict, max_length=100
    )

    @model_validator(mode="after")
    def validate_mode_value(self) -> "SpatialAppearance":
        selected = {
            "COLOR": self.color,
            "EMOJI": self.emoji,
            "IMAGE": self.asset_path,
            "SPRITE": self.asset_path,
        }[self.mode]
        if selected is None:
            raise ValueError(f"{self.mode} appearance is missing its visual value")
        if self.mode != "COLOR" and self.color is not None:
            raise ValueError("color is only valid for COLOR appearance")
        if self.mode != "EMOJI" and self.emoji is not None:
            raise ValueError("emoji is only valid for EMOJI appearance")
        if self.mode not in {"IMAGE", "SPRITE"} and self.asset_path is not None:
            raise ValueError("asset_path is only valid for IMAGE or SPRITE appearance")
        return self


class SpatialPhysics(StrictModel):
    collision: bool = False
    width_m: float = Field(default=1.0, gt=0, le=10_000)
    height_m: float = Field(default=1.0, gt=0, le=10_000)
    z_index: int = Field(default=0, ge=-10_000, le=10_000)
    traversable_by: list[Literal["PEDESTRIAN", "CAR", "BICYCLE", "MOTORCYCLE", "ALL"]] = Field(
        default_factory=lambda: ["ALL"], min_length=1, max_length=10
    )
    speed_limit_mps: float | None = Field(default=None, gt=0, le=200)

    @field_validator("traversable_by")
    @classmethod
    def unique_traversal_modes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("traversable_by values must be unique")
        if "ALL" in value and len(value) > 1:
            raise ValueError("ALL cannot be combined with explicit traversal modes")
        return value


class SpatialSemantics(StrictModel):
    tags: list[StableKey] = Field(default_factory=list, max_length=100)
    address_role: Literal["NONE", "SECTOR", "ARENA", "OBJECT"] = "NONE"
    surface: Literal[
        "GENERIC", "ROAD", "SIDEWALK", "CROSSWALK", "BUILDING", "GRASS", "WATER"
    ] = "GENERIC"
    emits_presence_events: bool = False

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("semantic tags must be unique")
        return value


class SpatialCapabilityAttachment(StrictModel):
    attachment_key: StableKey
    capability_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    capability_bundle_revision_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Attachments use the same external port and target vocabulary as an
    # experiment mount.  Keeping these bindings on the reusable asset makes a
    # traffic light, access gate, or vehicle executable wherever it is placed.
    target_bindings: dict[StableKey, AttachmentRef] = Field(default_factory=dict)
    input_bindings: dict[PortKey, AttachmentRef] = Field(default_factory=dict)
    output_bindings: dict[PortKey, AttachmentRef] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def require_one_revision(self) -> "SpatialCapabilityAttachment":
        if bool(self.capability_revision_id) == bool(self.capability_bundle_revision_id):
            raise ValueError(
                "attachment requires exactly one capability or capability bundle revision"
            )
        return self


class SpatialAssetContract(StrictModel):
    schema_version: Literal["ga-spatial-asset/v1"] = "ga-spatial-asset/v1"
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    summary: Annotated[str, StringConstraints(max_length=2_000)] = ""
    kind: SpatialAssetKind
    appearance: SpatialAppearance
    physics: SpatialPhysics = Field(default_factory=SpatialPhysics)
    semantics: SpatialSemantics = Field(default_factory=SpatialSemantics)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    capability_attachments: list[SpatialCapabilityAttachment] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_asset_relations(self) -> "SpatialAssetContract":
        keys = [item.attachment_key for item in self.capability_attachments]
        if len(keys) != len(set(keys)):
            raise ValueError("capability attachment keys must be unique")
        if self.kind == "ZONE" and self.physics.collision:
            raise ValueError("ZONE assets cannot be collidable")
        if self.kind == "MARKING" and self.physics.collision:
            raise ValueError("MARKING assets cannot be collidable")
        return self


class SpatialPlacement(StrictModel):
    instance_key: StableKey
    spatial_asset_revision_id: str = Field(min_length=1, max_length=36)
    x_m: float
    y_m: float
    rotation_degrees: float = Field(default=0, ge=-360, le=360)
    state_overrides: dict[str, Any] = Field(default_factory=dict)
    capability_parameter_overrides: dict[StableKey, dict[str, Any]] = Field(
        default_factory=dict
    )
    capability_target_overrides: dict[
        StableKey, dict[StableKey, AttachmentRef]
    ] = Field(default_factory=dict)
    capability_input_overrides: dict[
        StableKey, dict[PortKey, AttachmentRef]
    ] = Field(default_factory=dict)
    capability_output_overrides: dict[
        StableKey, dict[PortKey, AttachmentRef]
    ] = Field(default_factory=dict)


class SpatialSceneExtension(StrictModel):
    schema_version: Literal["ga-spatial-scene/v1"] = "ga-spatial-scene/v1"
    meters_per_tile: float = Field(default=1.0, gt=0, le=1_000)
    palette_refs: dict[StableKey, str] = Field(default_factory=dict, max_length=1_000)
    placements: list[SpatialPlacement] = Field(default_factory=list, max_length=100_000)

    @model_validator(mode="after")
    def validate_scene_keys(self) -> "SpatialSceneExtension":
        placement_keys = [item.instance_key for item in self.placements]
        if len(placement_keys) != len(set(placement_keys)):
            raise ValueError("spatial placement instance keys must be unique")
        return self


__all__ = [
    "SpatialAppearance",
    "SpatialAssetContract",
    "SpatialCapabilityAttachment",
    "SpatialPhysics",
    "SpatialPlacement",
    "SpatialSceneExtension",
    "SpatialSemantics",
]
