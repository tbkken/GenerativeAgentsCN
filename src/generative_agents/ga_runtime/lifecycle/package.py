"""Assemble execution solely from the shared, physically embedded resources."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from generative_agents.ga_protocol.schemas.experiment import ExperimentDefinition
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest, SkillPackageRegistry
from generative_agents.ga_protocol.packages.definition import (
    assemble_experiment_definition, assemble_skill_registry, read_experiment_assembly,
)
from generative_agents.ga_protocol.packages.resources import (
    ResourceSet, read_resource_index, resource_content_hash, skill_document,
)
from generative_agents.ga_protocol.packages.io import PackageError, verify_integrity
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory


@dataclass(frozen=True, slots=True)
class LoadedExperiment:
    root: Path
    manifest: ExperimentManifest
    definition: ExperimentDefinition
    skill_registry: SkillPackageRegistry
    skill_snapshot: dict[str, dict[str, object]]
    chat_api_key: str
    embedding_api_key: str
    root_sha256: str


def _runtime_model_config(document: dict, purpose: str) -> tuple[dict, str]:
    config = document.get(purpose)
    if not isinstance(config, dict):
        raise PackageError(f"models.{purpose} is required")
    config = dict(config)
    environment_name = config.pop("credential_env", None)
    if environment_name is not None:
        if not isinstance(environment_name, str) or not environment_name:
            raise PackageError(f"models.{purpose}.credential_env must be a non-empty name")
        if not all(character.isalnum() or character == "_" for character in environment_name):
            raise PackageError(f"unsafe credential environment name: {environment_name}")
    return config, os.environ.get(environment_name, "") if environment_name else ""


def _load_skill_snapshot(resources: ResourceSet) -> dict[str, dict[str, object]]:
    snapshot = {}
    for record in resources.resources:
        if record.kind != "skill":
            continue
        document = skill_document(record, resources)
        entrypoint = record.definition["entrypoint"]
        folder = PurePosixPath(entrypoint).parent
        files = {PurePosixPath(path).relative_to(folder).as_posix(): resources.files[path]
                 for path in record.attachments if path != entrypoint}
        scripts = {}
        for relative, content in files.items():
            if relative.startswith("scripts/"):
                try:
                    scripts[relative] = content.decode("utf-8-sig")
                except UnicodeError as exc:
                    raise PackageError(f"Skill script must be UTF-8 text: {record.key}/{relative}") from exc
        snapshot[record.key] = {"kind": record.definition["skill_kind"], "description": document.description,
            "markdown": document.markdown, "content_hash": resource_content_hash(record, resources),
            "scripts": scripts, "files": files}
    return snapshot


def load_experiment_directory(root: str | Path) -> LoadedExperiment:
    root = Path(root).resolve()
    manifest = validate_experiment_directory(root)
    integrity = verify_integrity(root)
    resources = read_resource_index(root, manifest.entrypoints.resources)
    assembly = read_experiment_assembly(root)
    raw = assemble_experiment_definition(manifest, resources, assembly)
    raw.pop("crowds", None)
    raw.pop("evaluation", None)
    raw["experiment"].pop("experiment_id", None)
    chat, chat_key = _runtime_model_config(raw["models"], "chat")
    embedding, embedding_key = _runtime_model_config(raw["models"], "embedding")
    raw["models"] = {"chat": chat, "embedding": embedding}
    return LoadedExperiment(root=root, manifest=manifest, definition=ExperimentDefinition.model_validate(raw),
        skill_registry=assemble_skill_registry(resources, assembly), skill_snapshot=_load_skill_snapshot(resources),
        chat_api_key=chat_key, embedding_api_key=embedding_key, root_sha256=str(integrity["root_sha256"]))
