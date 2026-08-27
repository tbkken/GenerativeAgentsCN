"""Versioned contracts for reusable map blocks, objects, zones and networks."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .schema import StrictModel
from .game_object_skills import GameObjectSkillBinding, validate_unique_skill_bindings


StableKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        max_length=80,
    ),
]
SpatialAssetKind = Literal["TILE", "OBJECT", "ZONE", "MARKING", "NETWORK"]
AppearanceMode = Literal["COLOR", "EMOJI", "IMAGE", "SPRITE"]


class SpatialAppearanceVariant(StrictModel):
    """空间资产在某个状态下使用的图片、裁剪区域和渲染参数。"""

    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    emoji: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)
        ]
        | None
    ) = None
    asset_path: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_one_value(self) -> "SpatialAppearanceVariant":
        """执行 `SpatialAppearanceVariant` 的`require``one``value`操作。

        返回:
            返回 `'SpatialAppearanceVariant'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if (
            sum(
                value is not None for value in (self.color, self.emoji, self.asset_path)
            )
            != 1
        ):
            raise ValueError("appearance variant requires exactly one visual value")
        return self


class SpatialAppearance(StrictModel):
    """空间资产的默认外观及按状态切换的视觉变体。"""

    mode: AppearanceMode
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    emoji: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)
        ]
        | None
    ) = None
    asset_path: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
        ]
        | None
    ) = None
    scale: float = Field(default=1.0, gt=0, le=100)
    rotation_degrees: float = Field(default=0, ge=-360, le=360)
    state_variants: dict[StableKey, SpatialAppearanceVariant] = Field(
        default_factory=dict, max_length=100
    )

    @model_validator(mode="after")
    def validate_mode_value(self) -> "SpatialAppearance":
        """校验`mode``value`。

        返回:
            返回 `'SpatialAppearance'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
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
    """空间资产的碰撞、占地和运动相关物理属性。"""

    collision: bool = False
    width_m: float = Field(default=1.0, gt=0, le=10_000)
    height_m: float = Field(default=1.0, gt=0, le=10_000)
    z_index: int = Field(default=0, ge=-10_000, le=10_000)
    traversable_by: list[
        Literal["PEDESTRIAN", "CAR", "BICYCLE", "MOTORCYCLE", "ALL"]
    ] = Field(default_factory=lambda: ["ALL"], min_length=1, max_length=10)
    speed_limit_mps: float | None = Field(default=None, gt=0, le=200)

    @field_validator("traversable_by")
    @classmethod
    def unique_traversal_modes(cls, value: list[str]) -> list[str]:
        """执行 `SpatialPhysics` 的`unique``traversal``modes`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`list[str]`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if len(value) != len(set(value)):
            raise ValueError("traversable_by values must be unique")
        if "ALL" in value and len(value) > 1:
            raise ValueError("ALL cannot be combined with explicit traversal modes")
        return value


class SpatialSemantics(StrictModel):
    """供智能体感知、寻路和 Skill 使用的语义标签与公开状态。"""

    tags: list[StableKey] = Field(default_factory=list, max_length=100)
    address_role: Literal["NONE", "SECTOR", "ARENA", "OBJECT"] = "NONE"
    surface: Literal[
        "GENERIC", "ROAD", "SIDEWALK", "CROSSWALK", "BUILDING", "GRASS", "WATER"
    ] = "GENERIC"
    emits_presence_events: bool = False

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """执行 `SpatialSemantics` 的`unique``tags`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`list[str]`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if len(value) != len(set(value)):
            raise ValueError("semantic tags must be unique")
        return value


class SpatialAssetContract(StrictModel):
    """可复用空间资产的完整版本化契约，组合外观、物理和语义。"""

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
    skill_bindings: list[GameObjectSkillBinding] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("skill_bindings")
    @classmethod
    def unique_skill_bindings(
        cls, value: list[GameObjectSkillBinding]
    ) -> list[GameObjectSkillBinding]:
        """执行 `SpatialAssetContract` 的`unique`技能`bindings`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`list[GameObjectSkillBinding]`。

        返回:
            返回按接口约定组织的结果集合。
        """
        return validate_unique_skill_bindings(value)

    @model_validator(mode="after")
    def validate_asset_relations(self) -> "SpatialAssetContract":
        """校验资源`relations`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.kind == "ZONE" and self.physics.collision:
            raise ValueError("ZONE assets cannot be collidable")
        if self.kind == "MARKING" and self.physics.collision:
            raise ValueError("MARKING assets cannot be collidable")
        if self.skill_bindings and self.kind != "OBJECT":
            raise ValueError("only OBJECT spatial assets may bind passive Skills")
        return self


class SpatialPlacement(StrictModel):
    """在具体地图坐标上实例化某个已发布空间资产。"""

    instance_key: StableKey
    spatial_asset_revision_id: str = Field(min_length=1, max_length=36)
    x_m: float
    y_m: float
    rotation_degrees: float = Field(default=0, ge=-360, le=360)
    state_overrides: dict[str, Any] = Field(default_factory=dict)


class SpatialSceneExtension(StrictModel):
    """附加到 WorldConfig 的版本化空间场景和全部资产实例。"""

    schema_version: Literal["ga-spatial-scene/v1"] = "ga-spatial-scene/v1"
    meters_per_tile: float = Field(default=1.0, gt=0, le=1_000)
    palette_refs: dict[StableKey, str] = Field(default_factory=dict, max_length=1_000)
    placements: list[SpatialPlacement] = Field(default_factory=list, max_length=100_000)

    @model_validator(mode="after")
    def validate_scene_keys(self) -> "SpatialSceneExtension":
        """校验`scene``keys`。

        返回:
            返回 `'SpatialSceneExtension'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        placement_keys = [item.instance_key for item in self.placements]
        if len(placement_keys) != len(set(placement_keys)):
            raise ValueError("spatial placement instance keys must be unique")
        return self


__all__ = [
    "SpatialAppearance",
    "SpatialAssetContract",
    "SpatialPhysics",
    "SpatialPlacement",
    "SpatialSceneExtension",
    "SpatialSemantics",
    "StableKey",
]
