"""Composable capability mounts for reusable brain revisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .capabilities import StableKey
from .schema import StrictModel


BrainCapabilityCategory = Literal[
    "SCHEDULE_STATE",
    "PERCEPTION_MEMORY",
    "ACTION_SPACE",
    "SOCIAL",
    "REFLECTION",
    "CUSTOM",
]


class BrainCapabilityMount(StrictModel):
    mount_key: StableKey
    category: BrainCapabilityCategory
    capability_bundle_revision_id: str = Field(min_length=1, max_length=36)
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class BrainCapabilityExtension(StrictModel):
    schema_version: Literal["ga-brain-extension/v1"] = "ga-brain-extension/v1"
    mounts: list[BrainCapabilityMount] = Field(default_factory=list, max_length=100)
    default_reasoning_interval_ms: int = Field(
        default=60_000, ge=100, le=86_400_000
    )
    legacy_workflow_adapter_enabled: bool = True

    @field_validator("mounts")
    @classmethod
    def unique_mount_keys(
        cls, value: list[BrainCapabilityMount]
    ) -> list[BrainCapabilityMount]:
        keys = [item.mount_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("brain capability mount keys must be unique")
        return value

    @model_validator(mode="after")
    def require_execution_path(self) -> "BrainCapabilityExtension":
        if not self.legacy_workflow_adapter_enabled and not any(
            item.enabled for item in self.mounts
        ):
            raise ValueError(
                "brain requires an enabled capability mount or the legacy workflow adapter"
            )
        return self


__all__ = ["BrainCapabilityExtension", "BrainCapabilityMount"]
