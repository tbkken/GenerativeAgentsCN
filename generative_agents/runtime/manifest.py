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
    WorkflowDefinition,
    canonical_json_bytes,
    definition_hash,
    get_algorithm_profile,
    workflow_bundle_hash,
)

from .context import RunPaths
from .capability_snapshot import (
    CAPABILITY_SNAPSHOT_SCHEMA_VERSION,
    capability_snapshot_hash,
)


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

    @property
    def workflows(self) -> Mapping[str, WorkflowDefinition]:
        return {
            key: WorkflowDefinition.model_validate(value)
            for key, value in (self.document.get("workflows") or {}).items()
        }

    @property
    def workflow_functions(self) -> Mapping[str, str]:
        return {
            str(key): str(value)
            for key, value in (self.document.get("workflow_functions") or {}).items()
        }

    @property
    def capability_snapshot(self) -> Mapping[str, Any] | None:
        value = self.document.get("capability_snapshot")
        return value if isinstance(value, Mapping) else None


def workflow_function_bundle_hash(functions: Mapping[str, str]) -> str:
    normalized = {str(key): str(value) for key, value in sorted(functions.items())}
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


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
    workflows: Mapping[str, WorkflowDefinition | Mapping[str, Any]] | None = None,
    workflow_functions: Mapping[str, str] | None = None,
    capability_snapshot: Mapping[str, Any] | None = None,
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
    if workflows:
        workflow_documents = {
            key: (
                value.model_dump(mode="json", exclude_none=False)
                if isinstance(value, WorkflowDefinition)
                else WorkflowDefinition.model_validate(value).model_dump(
                    mode="json", exclude_none=False
                )
            )
            for key, value in sorted(workflows.items())
        }
        envelope["workflows"] = workflow_documents
        envelope["workflow_bundle_hash"] = workflow_bundle_hash(workflow_documents)
        function_documents = {
            str(key): str(value)
            for key, value in sorted((workflow_functions or {}).items())
        }
        envelope["workflow_functions"] = function_documents
        envelope["workflow_function_bundle_hash"] = workflow_function_bundle_hash(
            function_documents
        )
    if capability_snapshot is not None:
        snapshot_document = dict(capability_snapshot)
        envelope["capability_snapshot"] = snapshot_document
        envelope["capability_snapshot_hash"] = capability_snapshot_hash(
            snapshot_document
        )
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
        workflows: Mapping[str, WorkflowDefinition | Mapping[str, Any]] | None = None,
        workflow_functions: Mapping[str, str] | None = None,
        capability_snapshot: Mapping[str, Any] | None = None,
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
        expected_workflows = None
        if workflows:
            expected_workflows = {
                key: (
                    value.model_dump(mode="json", exclude_none=False)
                    if isinstance(value, WorkflowDefinition)
                    else WorkflowDefinition.model_validate(value).model_dump(
                        mode="json", exclude_none=False
                    )
                )
                for key, value in sorted(workflows.items())
            }
        expected_functions = {
            str(key): str(value)
            for key, value in sorted((workflow_functions or {}).items())
        }
        expected_capability_snapshot = (
            dict(capability_snapshot) if capability_snapshot is not None else None
        )
        has_function_bundle = "workflow_functions" in document
        function_bundle_matches = (
            (
                document.get("workflow_functions") == expected_functions
                and document.get("workflow_function_bundle_hash")
                == workflow_function_bundle_hash(expected_functions)
            )
            if has_function_bundle
            else not expected_functions
        )
        matches = (
            document.get("experiment_id") == str(experiment_id)
            and document.get("revision_id") == str(revision_id)
            and document.get("definition_hash") == actual_definition_hash
            and document.get("definition")
            == definition.model_dump(mode="json", exclude_none=False)
            and document.get("algorithm_version") == definition.engine.algorithm_version
            and document.get("assets") == expected_assets
            and document.get("workflows") == expected_workflows
            and document.get("workflow_bundle_hash")
            == (
                workflow_bundle_hash(expected_workflows)
                if expected_workflows is not None
                else None
            )
            and function_bundle_matches
            and document.get("capability_snapshot")
            == expected_capability_snapshot
            and document.get("capability_snapshot_hash")
            == (
                capability_snapshot_hash(expected_capability_snapshot)
                if expected_capability_snapshot is not None
                else None
            )
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
        workflow_documents = document.get("workflows")
        workflow_digest = document.get("workflow_bundle_hash")
        if (workflow_documents is None) != (workflow_digest is None):
            raise ValueError("manifest workflow bundle is incomplete")
        if workflow_documents is not None:
            normalized_workflows = {
                key: WorkflowDefinition.model_validate(value).model_dump(
                    mode="json", exclude_none=False
                )
                for key, value in sorted(workflow_documents.items())
            }
            if workflow_bundle_hash(normalized_workflows) != workflow_digest:
                raise ValueError("manifest workflow_bundle_hash mismatch")
        function_documents = document.get("workflow_functions")
        function_digest = document.get("workflow_function_bundle_hash")
        if (function_documents is None) != (function_digest is None):
            raise ValueError("manifest workflow Function bundle is incomplete")
        if function_documents is not None:
            if not isinstance(function_documents, Mapping) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in function_documents.items()
            ):
                raise ValueError("manifest workflow Function bundle is invalid")
            if workflow_function_bundle_hash(function_documents) != function_digest:
                raise ValueError("manifest workflow_function_bundle_hash mismatch")
        capability_snapshot = document.get("capability_snapshot")
        capability_digest = document.get("capability_snapshot_hash")
        if (capability_snapshot is None) != (capability_digest is None):
            raise ValueError("manifest capability snapshot is incomplete")
        if capability_snapshot is not None:
            if not isinstance(capability_snapshot, dict):
                raise ValueError("manifest capability snapshot is invalid")
            if (
                capability_snapshot.get("schema_version")
                != CAPABILITY_SNAPSHOT_SCHEMA_VERSION
            ):
                raise ValueError("unsupported capability snapshot schema version")
            if capability_snapshot_hash(capability_snapshot) != capability_digest:
                raise ValueError("manifest capability_snapshot_hash mismatch")
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
