"""Content-bound provenance for generated Run artifacts, without a database."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field, ValidationError

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.schemas.manifests import ProtocolModel
from generative_agents.ga_protocol.schemas.manifests import RunState
from generative_agents.ga_protocol.schemas.manifests import RunStatus


class ArtifactProvenance(ProtocolModel):
    schema_version: int = 1
    run_id: str
    logical_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    source_step: int = Field(ge=0)
    source_total_steps: int = Field(ge=1)
    source_status: RunState
    partial: bool
    generated_at: datetime
    generator_version: str = "ga-web-export-v1"


def provenance_path(root: Path, logical_name: str, digest: str) -> Path:
    # Different filtered exports may contain identical bytes at different Steps.
    # Bind the record to the logical artifact as well as its full content hash.
    key = hashlib.sha256(f"{logical_name}\0{digest}".encode("utf-8")).hexdigest()
    return root / "artifact-metadata" / f"{key}.json"


def record_artifact_provenance(
    root: Path, artifact: Path, status: RunStatus, *, source_step: int | None = None
) -> Path:
    content = artifact.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    logical_name = artifact.relative_to(root / "artifacts").as_posix()
    target = provenance_path(root, logical_name, digest)
    # Reusing an unchanged content-addressed report must not change its origin.
    if target.is_file():
        return target
    step = status.committed_step if source_step is None else source_step
    if step > status.committed_step:
        raise PackageError("artifact source exceeds the committed Run boundary")
    record = ArtifactProvenance(
        run_id=status.run_id,
        logical_name=logical_name,
        sha256=digest,
        size_bytes=len(content),
        source_step=step,
        source_total_steps=status.total_steps,
        source_status=status.status,
        partial=status.status != RunState.COMPLETED or step < status.total_steps,
        generated_at=datetime.now(UTC),
    )
    atomic_write_json(target, record.model_dump(mode="json"))
    return target


def read_artifact_provenance(
    root: Path, *, run_id: str, logical_name: str, digest: str, size_bytes: int
) -> dict:
    """Never infer historical origin from a filename, mtime or live Run state."""
    unknown = {
        "provenance_status": "UNKNOWN",
        "source_step": None,
        "source_total_steps": None,
        "source_status": None,
        "partial": None,
        "generated_at": None,
        "generator_version": "unknown",
    }
    path = provenance_path(root, logical_name, digest)
    if not path.is_file() or path.is_symlink():
        return unknown
    try:
        record = ArtifactProvenance.model_validate(read_json(path))
    except (PackageError, ValidationError):
        return unknown
    if (
        record.schema_version != 1
        or record.run_id != run_id
        or record.logical_name != logical_name
        or record.sha256 != digest
        or record.size_bytes != size_bytes
        or record.source_step > record.source_total_steps
        or record.generated_at.utcoffset() is None
        or record.partial != (
            record.source_status != RunState.COMPLETED
            or record.source_step < record.source_total_steps
        )
    ):
        return unknown
    return {
        "provenance_status": "RECORDED",
        **record.model_dump(mode="json", include={
            "source_step", "source_total_steps", "source_status", "partial",
            "generated_at", "generator_version",
        }),
    }
