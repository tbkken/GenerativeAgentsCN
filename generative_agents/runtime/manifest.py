"""Materialize and verify the immutable manifest consumed by a run worker."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid4

from generative_agents.config import (
    ExperimentDefinition,
    canonical_json_bytes,
    definition_hash,
    get_algorithm_profile,
)

from .context import RunPaths


MANIFEST_SCHEMA_VERSION = 1


class ManifestConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedRunManifest:
    path: Path
    manifest_hash: str
    document: Mapping[str, Any]

    @property
    def definition(self) -> ExperimentDefinition:
        return ExperimentDefinition.model_validate(self.document["definition"])


def collect_dependency_versions(
    distributions: Iterable[str] = (
        "pydantic",
        "SQLAlchemy",
        "fastapi",
        "llama-index-core",
        "openai",
    ),
) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in distributions:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def build_manifest_document(
    *,
    run_id: UUID,
    experiment_id: UUID,
    revision_id: UUID,
    definition: ExperimentDefinition,
    expected_definition_hash: str,
    code_build_id: str,
    assets: Iterable[Mapping[str, Any]],
    materialized_at: datetime,
    dependency_versions: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    actual_definition_hash = definition_hash(definition)
    if actual_definition_hash != expected_definition_hash:
        raise ValueError("revision definition_hash does not match normalized definition")
    if materialized_at.tzinfo is None:
        raise ValueError("materialized_at must be timezone-aware")
    if not code_build_id.strip():
        raise ValueError("code_build_id must not be empty")
    algorithm_version = definition.engine.algorithm_version
    get_algorithm_profile(algorithm_version)
    asset_list = sorted(
        (dict(asset) for asset in assets),
        key=lambda item: (str(item.get("logical_path", "")), str(item.get("asset_hash", ""))),
    )
    envelope: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": str(run_id),
        "experiment_id": str(experiment_id),
        "revision_id": str(revision_id),
        "definition_hash": actual_definition_hash,
        "definition": definition.model_dump(mode="json", exclude_none=False),
        "algorithm_version": algorithm_version,
        "code_build_id": code_build_id.strip(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "dependency_versions": dict(
            dependency_versions
            if dependency_versions is not None
            else collect_dependency_versions()
        ),
        "assets": asset_list,
        "materialized_at": materialized_at.isoformat(),
    }
    envelope["manifest_hash"] = hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    return envelope


class RunManifestStore:
    def __init__(self, paths: RunPaths):
        self._paths = paths
        self._paths.ensure()

    def materialize(self, document: Mapping[str, Any]) -> VerifiedRunManifest:
        self._verify_document(document)
        content = canonical_json_bytes(document)
        target = self._paths.manifest
        if target.exists():
            existing = target.read_bytes()
            if existing != content:
                raise ManifestConflictError("run manifest is immutable and already differs")
            return self.load_verified()
        temporary = self._paths.temporary / f"manifest-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return self.load_verified()

    def reuse_for_revision(
        self,
        *,
        experiment_id: UUID,
        revision_id: UUID,
        definition: ExperimentDefinition,
        expected_definition_hash: str,
        assets: Iterable[Mapping[str, Any]],
    ) -> VerifiedRunManifest:
        """Verify an existing Run manifest against its published Revision.

        Resume attempts must consume the first attempt's immutable manifest.
        They deliberately do not compare deployment-time provenance such as
        ``materialized_at`` or the currently running service's build ID.
        """

        verified = self.load_verified()
        actual_definition_hash = definition_hash(definition)
        if actual_definition_hash != expected_definition_hash:
            raise ManifestConflictError(
                "published Revision definition no longer matches its definition hash"
            )
        expected_assets = sorted(
            (dict(asset) for asset in assets),
            key=lambda item: (
                str(item.get("logical_path", "")),
                str(item.get("asset_hash", "")),
            ),
        )
        document = verified.document
        matches = (
            document.get("experiment_id") == str(experiment_id)
            and document.get("revision_id") == str(revision_id)
            and document.get("definition_hash") == actual_definition_hash
            and document.get("definition")
            == definition.model_dump(mode="json", exclude_none=False)
            and document.get("algorithm_version") == definition.engine.algorithm_version
            and document.get("assets") == expected_assets
        )
        if not matches:
            raise ManifestConflictError(
                "run manifest does not match the claimed published Revision"
            )
        return verified

    def exists(self) -> bool:
        target = self._paths.manifest
        return target.exists() or target.is_symlink()

    def load_verified(self) -> VerifiedRunManifest:
        target = self._paths.manifest
        with target.open("r", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        manifest_hash = self._verify_document(document)
        return VerifiedRunManifest(
            path=target,
            manifest_hash=manifest_hash,
            document=document,
        )

    def _verify_document(self, document: Mapping[str, Any]) -> str:
        if document.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema version")
        if document.get("run_id") != str(self._paths.run_id):
            raise ValueError("manifest run_id does not own this run directory")
        expected_manifest_hash = document.get("manifest_hash")
        if not isinstance(expected_manifest_hash, str):
            raise ValueError("manifest_hash is missing")
        unsigned = dict(document)
        unsigned.pop("manifest_hash", None)
        actual_manifest_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        if actual_manifest_hash != expected_manifest_hash:
            raise ValueError("manifest_hash mismatch")
        definition = ExperimentDefinition.model_validate(document.get("definition"))
        if definition_hash(definition) != document.get("definition_hash"):
            raise ValueError("manifest definition_hash mismatch")
        algorithm_version = document.get("algorithm_version")
        if algorithm_version != definition.engine.algorithm_version:
            raise ValueError("manifest algorithm_version mismatch")
        get_algorithm_profile(algorithm_version)
        return actual_manifest_hash

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
