"""Versioned contracts for tools that exist in the simulated world."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .schema import StrictModel
from .spatial_assets import SpatialAppearance, StableKey


ToolKind = Literal[
    "CAR",
    "BICYCLE",
    "MOTORCYCLE",
    "ACCESS_CARD",
    "DEVICE",
    "OTHER",
]


class ToolMobility(StrictModel):
    """工具携带、停放和移动时需要遵守的空间规则。"""

    mode: Literal["NONE", "ROAD", "BICYCLE_NETWORK", "PEDESTRIAN_NETWORK"] = "NONE"
    max_speed_mps: float = Field(default=0, ge=0, le=200)
    max_acceleration_mps2: float = Field(default=0, ge=0, le=100)
    max_deceleration_mps2: float = Field(default=0, ge=0, le=100)
    operator_required: bool = False
    capacity: int = Field(default=1, ge=1, le=500)

    @model_validator(mode="after")
    def validate_motion(self) -> "ToolMobility":
        """校验`motion`。

        返回:
            返回 `'ToolMobility'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        motion_limits = (
            self.max_speed_mps,
            self.max_acceleration_mps2,
            self.max_deceleration_mps2,
        )
        if self.mode == "NONE" and any(value > 0 for value in motion_limits):
            raise ValueError("non-mobile tools cannot declare motion limits")
        if self.mode != "NONE" and self.max_speed_mps <= 0:
            raise ValueError("mobile tools require a positive max_speed_mps")
        return self


class ToolContract(StrictModel):
    """智能体可拥有或控制的版本化工具及其状态、能力和移动契约。"""

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

    @model_validator(mode="after")
    def validate_relations(self) -> "ToolContract":
        """校验`relations`。

        返回:
            返回 `'ToolContract'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tool tags must be unique")
        if len(self.interfaces) != len(set(self.interfaces)):
            raise ValueError("tool interfaces must be unique")
        vehicle_kinds = {"CAR", "BICYCLE", "MOTORCYCLE"}
        if self.kind in vehicle_kinds and self.mobility.mode == "NONE":
            raise ValueError("vehicle tools must declare a mobility mode")
        if self.kind not in vehicle_kinds and self.mobility.mode != "NONE":
            raise ValueError("only vehicle tools may declare mobility")
        return self


__all__ = ["ToolContract", "ToolMobility"]
