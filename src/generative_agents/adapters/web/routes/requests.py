"""Validated HTTP request bodies."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from generative_agents.ga_studio.api import StudioAgentDefinition

class PortableRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

class AgentResourceCreate(PortableRequest):
    definition: StudioAgentDefinition
    description: str = ''

class AgentResourceUpdate(AgentResourceCreate):
    row_version: int = Field(ge=1)

class CrowdResourceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = ''
    crowd_key: str | None = None
    agent_ids: list[str] = Field(default_factory=list)

class CrowdResourceUpdate(PortableRequest):
    row_version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    agent_ids: list[str] = Field(default_factory=list)

class DocumentResourceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = ''
    key: str | None = None
    config: dict

class DocumentResourceUpdate(PortableRequest):
    row_version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    config: dict

class PlacementRequest(PortableRequest):
    agent_id: str
    coord: tuple[int, int]

class ExperimentWorkspaceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(default='', max_length=20000)
    owner: str = Field(default='', max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    key: str | None = None
    timezone: str = 'Asia/Shanghai'
    map_id: str
    brain_skill_id: str
    model_preset_id: str
    embedding_model_preset_id: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
    crowd_ids: list[str] = Field(default_factory=list)
    evaluator_ids: list[str] = Field(default_factory=list)
    placements: list[PlacementRequest] = Field(default_factory=list)
    simulation: dict | None = None

class ExperimentEntrypointUpdate(PortableRequest):
    document: dict

class ExperimentDefinitionUpdate(PortableRequest):
    definition: dict
    expected_content_sha256: str | None = None

class ResumeRunRequest(PortableRequest):
    checkpoint_step: int | None = Field(default=None, ge=1)
    expected_attempt_id: str | None = None

class SecretCreate(PortableRequest):
    kind: str
    value: str = Field(min_length=1)

class ArtifactJobCreate(PortableRequest):
    job_type: str
    parameters: dict = Field(default_factory=dict)

class ExperimentBatchRequest(PortableRequest):
    experiment_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal['ARCHIVE', 'RESTORE']
