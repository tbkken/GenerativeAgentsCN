"""Environment-specific map content, independent of authoring and execution."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SimWorldMap(BaseModel):
    """Only the external catalogue identity is portable. SimWorld owns its content."""
    model_config = ConfigDict(extra="forbid")
    environment_type: Literal["spatial_3d"] = "spatial_3d"
    simworld_scene_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")


class SocialWorldDefinition(BaseModel):
    """One shared public conversation space; members belong to the experiment."""

    model_config = ConfigDict(extra="forbid")
    environment_type: Literal["social_1d"] = "social_1d"
    presentation: Literal["chat", "forum"]
    description: str = Field(default="", max_length=10_000)
    rules: str = Field(default="", max_length=20_000)


def environment_type(world: object) -> str:
    """Return the supported environment kind without interpreting geometry."""
    if hasattr(world, "definition"):
        definition = world.definition
    elif isinstance(world, dict):
        definition = world.get("definition", world)
    else:
        raise ValueError("world must contain an environment definition")
    kind = definition.get("environment_type", "spatial_2d")
    if kind not in {"social_1d", "spatial_2d", "spatial_3d"}:
        raise ValueError(f"unsupported environment type: {kind}")
    return kind
