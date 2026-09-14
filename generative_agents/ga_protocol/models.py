"""Schema models for the portable experiment and Run package protocols."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .constants import EXPERIMENT_PROTOCOL, PROTOCOL_VERSION, RUN_PROTOCOL


SafePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def validate_uuid(value: str) -> str:
    """Return a canonical UUID string without assigning filename semantics."""

    return str(UUID(str(value)))


def validate_package_path(value: str) -> str:
    """Normalize and validate a package-internal POSIX path."""

    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("package path must be a safe relative POSIX path")
    return path.as_posix()


class ProtocolModel(BaseModel):
    """Strict base model so protocol extensions are explicit migrations."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExperimentIdentity(ProtocolModel):
    experiment_id: str
    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    goal: Annotated[str, StringConstraints(max_length=20_000)] = ""
    timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = "Asia/Shanghai"

    _canonical_id = field_validator("experiment_id")(validate_uuid)


class ExperimentEntrypoints(ProtocolModel):
    world: SafePath
    semantic_index: SafePath
    agents: SafePath
    skills: SafePath
    models: SafePath
    simulation: SafePath
    engine: SafePath
    evaluation: SafePath | None = None

    _safe_paths = field_validator(
        "world", "semantic_index", "agents", "skills", "models", "simulation", "engine", "evaluation"
    )(lambda value: validate_package_path(value) if value is not None else None)


class ExperimentManifest(ProtocolModel):
    protocol: Literal["ga-experiment"] = EXPERIMENT_PROTOCOL
    schema_version: Literal[1] = PROTOCOL_VERSION
    package_kind: Literal["experiment"] = "experiment"
    experiment: ExperimentIdentity
    created_at: datetime
    entrypoints: ExperimentEntrypoints
    metadata: dict = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must contain a UTC offset")
        return value


class SkillPackageEntry(ProtocolModel):
    skill_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    kind: Literal["brain", "sub_skill", "object"]
    editor_kind: Literal["brain", "pack", "atomic"] | None = None
    path: SafePath
    dependencies: list[str] = Field(default_factory=list)
    content_sha256: Sha256 | None = None

    _safe_path = field_validator("path")(validate_package_path)


class SkillPackageRegistry(ProtocolModel):
    schema_version: Literal[1] = 1
    brain_skill: str
    object_roots: list[str] = Field(default_factory=list)
    skills: list[SkillPackageEntry]


class RunLineage(ProtocolModel):
    mode: Literal["start", "rerun"] = "start"
    origin_run_id: str | None = None

    _canonical_origin = field_validator("origin_run_id")(
        lambda value: validate_uuid(value) if value is not None else None
    )


class EmbeddedExperiment(ProtocolModel):
    experiment_id: str
    path: SafePath = "experiment"
    root_sha256: Sha256

    _canonical_id = field_validator("experiment_id")(validate_uuid)
    _safe_path = field_validator("path")(validate_package_path)


class RunManifest(ProtocolModel):
    protocol: Literal["ga-run"] = RUN_PROTOCOL
    schema_version: Literal[1] = PROTOCOL_VERSION
    package_kind: Literal["run"] = "run"
    run_id: str
    created_at: datetime
    requested_steps: int = Field(ge=1)
    experiment: EmbeddedExperiment
    lineage: RunLineage = Field(default_factory=RunLineage)

    _canonical_id = field_validator("run_id")(validate_uuid)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must contain a UTC offset")
        return value


class RunState(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunStatus(ProtocolModel):
    run_id: str
    status: RunState = RunState.CREATED
    active_attempt_id: str | None = None
    committed_step: int = Field(default=0, ge=0)
    total_steps: int = Field(ge=1)
    updated_at: datetime
    reason: str | None = None

    _canonical_run_id = field_validator("run_id")(validate_uuid)
    _canonical_attempt_id = field_validator("active_attempt_id")(
        lambda value: validate_uuid(value) if value is not None else None
    )


class AttemptState(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AttemptRecord(ProtocolModel):
    attempt_id: str
    run_id: str
    ordinal: int = Field(ge=1)
    resumed_from_step: int = Field(default=0, ge=0)
    status: AttemptState = AttemptState.RUNNING
    started_at: datetime
    finished_at: datetime | None = None
    failure: str | None = None

    _canonical_attempt_id = field_validator("attempt_id")(validate_uuid)
    _canonical_run_id = field_validator("run_id")(validate_uuid)
