"""Load the physical experiment contents into the existing simulation kernel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from generative_agents.ga_protocol.schemas.experiment import ExperimentDefinition
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import SkillPackageRegistry
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.packages.io import verify_integrity


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


def _without_schema(document: object, name: str) -> dict:
    if not isinstance(document, dict):
        raise PackageError(f"experiment {name} entrypoint must be a JSON object")
    value = dict(document)
    value.pop("schema_version", None)
    return value


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
    api_key = os.environ.get(environment_name, "") if environment_name else ""
    return config, api_key


def _load_skill_snapshot(root: Path, registry: SkillPackageRegistry) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for entry in registry.skills:
        skill_path = root / entry.path
        scripts: dict[str, str] = {}
        scripts_root = skill_path.parent / "scripts"
        if scripts_root.is_dir():
            for script in sorted(scripts_root.rglob("*")):
                if script.is_file():
                    scripts[script.relative_to(skill_path.parent).as_posix()] = script.read_text(
                        encoding="utf-8-sig"
                    )
        snapshot[entry.skill_id] = {
            "kind": "brain" if entry.kind == "brain" else "atomic",
            "description": "physically embedded experiment Skill",
            "markdown": skill_path.read_text(encoding="utf-8-sig"),
            # This hash is a content check used by the legacy parser, not an
            # author-resource version or a cross-package relationship.
            "content_hash": entry.content_sha256 or "",
            "scripts": scripts,
        }
    return snapshot


def load_experiment_directory(root: str | Path) -> LoadedExperiment:
    root = Path(root).resolve()
    manifest = validate_experiment_directory(root)
    integrity = verify_integrity(root)
    entrypoints = manifest.entrypoints
    world = _without_schema(read_json(root / entrypoints.world), "world")
    agents_document = _without_schema(read_json(root / entrypoints.agents), "agents")
    models_document = _without_schema(read_json(root / entrypoints.models), "models")
    simulation = _without_schema(read_json(root / entrypoints.simulation), "simulation")
    engine = _without_schema(read_json(root / entrypoints.engine), "engine")
    evaluation = (
        _without_schema(read_json(root / entrypoints.evaluation), "evaluation")
        if entrypoints.evaluation
        else {}
    )
    results = evaluation.get("results") if isinstance(evaluation, dict) else None
    if not isinstance(results, dict):
        # Early protocol-v1 packages stored ResultsConfig at the entrypoint root.
        results = evaluation
    agents = agents_document.get("agents")
    if not isinstance(agents, list):
        raise PackageError("agents entrypoint must contain an agents array")
    chat, chat_key = _runtime_model_config(models_document, "chat")
    embedding, embedding_key = _runtime_model_config(models_document, "embedding")
    definition = ExperimentDefinition.model_validate(
        {
            "schema_version": 1,
            "experiment": {
                "key": manifest.experiment.key,
                "name": manifest.experiment.name,
                "goal": manifest.experiment.goal,
                "timezone": manifest.experiment.timezone,
            },
            "engine": engine,
            "simulation": simulation,
            "results": results,
            "models": {"chat": chat, "embedding": embedding},
            "world": world,
            "agents": agents,
        }
    )
    registry_document = read_json(root / entrypoints.skills)
    skill_registry = SkillPackageRegistry.model_validate(registry_document)
    return LoadedExperiment(
        root=root,
        manifest=manifest,
        definition=definition,
        skill_registry=skill_registry,
        skill_snapshot=_load_skill_snapshot(root, skill_registry),
        chat_api_key=chat_key,
        embedding_api_key=embedding_key,
        root_sha256=str(integrity["root_sha256"]),
    )
