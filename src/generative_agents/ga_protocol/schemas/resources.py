"""Shared portable resource content and experiment-only assembly contracts."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from generative_agents.ga_protocol.schemas.manifests import ProtocolModel, Sha256, validate_package_path
from generative_agents.ga_protocol.schemas.experiment import AgentPerceptionLimits, AgentScratch

ResourceKind = Literal["map", "agent", "crowd", "skill", "model", "evaluator", "spatial_asset"]
ResourceKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120,
                                              pattern=r"^[a-z0-9][a-z0-9_-]*$")]


class ResourceRef(ProtocolModel):
    kind: ResourceKind
    key: ResourceKey
    expected_sha256: Sha256 | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return self.kind, self.key


class ResourceRecord(ResourceRef):
    # A resource is content, not an expectation about somebody else's content.
    expected_sha256: None = Field(default=None, exclude=True)
    name: str = ""
    description: str = ""
    definition: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[ResourceRef] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)

    @field_validator("attachments")
    @classmethod
    def safe_attachments(cls, value: list[str]) -> list[str]:
        return [validate_package_path(path) for path in value]


class ResourceIndex(ProtocolModel):
    schema_version: Literal[2] = 2
    roots: list[ResourceRef] = Field(default_factory=list)
    resources: list[ResourceRecord] = Field(default_factory=list)


class AgentCore(ProtocolModel):
    """Map-independent content. Identity lives in ResourceRecord."""
    enabled: bool = True
    portrait_asset: str | None = None
    sprite_asset: str | None = None
    sprite_layout: Literal["4x3", "4x4"] = "4x4"
    sprite_display_tiles: float | None = Field(default=None, ge=0.5, le=6.0)
    model_override: str | None = None
    tags: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    currently: str = ""
    scratch: AgentScratch
    perception: AgentPerceptionLimits = Field(default_factory=AgentPerceptionLimits)

    _asset_path = field_validator("portrait_asset", "sprite_asset")(
        lambda path: validate_package_path(path) if path is not None else path
    )


class ExperimentPlacement(ProtocolModel):
    agent: ResourceRef
    coord: tuple[int, int]
    spatial: dict[str, Any]


class ExperimentAssembly(ProtocolModel):
    schema_version: Literal[2] = 2
    map: ResourceRef
    brain: ResourceRef
    placements: list[ExperimentPlacement] = Field(default_factory=list)
    crowds: list[ResourceRef] = Field(default_factory=list)
    models: dict[Literal["chat", "embedding"], ResourceRef]
    simulation: dict[str, Any]
    engine: dict[str, Any] = Field(default_factory=lambda: {"algorithm_version": "ga-cn-v1"})
    results: dict[str, Any] = Field(default_factory=dict)
    evaluators: list[ResourceRef] = Field(default_factory=list)
