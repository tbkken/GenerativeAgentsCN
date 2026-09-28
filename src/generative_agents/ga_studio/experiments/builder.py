"""Compile selected author content into the shared protocol and experiment assembly."""
from __future__ import annotations

import copy
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from generative_agents.ga_protocol.packages.definition import write_experiment_definition
from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_write_json, checked_package_path, iter_package_files,
    seal_directory, write_integrity_manifest,
)
from generative_agents.ga_protocol.packages.resources import ResourceRef, ResourceRecord, ResourceSet, skill_document
from generative_agents.ga_protocol.packages.semantic_index import build_semantic_index
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.manifests import (
    ExperimentEntrypoints, ExperimentIdentity, ExperimentManifest, validate_package_path,
)


@dataclass(frozen=True, slots=True)
class SkillSource:
    """Author input; usage role is resolved into the experiment assembly."""
    skill_id: str
    kind: Literal["brain", "sub_skill", "object"]
    skill_file: Path
    dependencies: tuple[str, ...] = ()
    editor_kind: Literal["brain", "pack", "atomic"] | None = None


def _plain_mapping(value: object) -> dict:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise PackageError("experiment definition must be a mapping")
    return copy.deepcopy(dict(value))


def _portable_content(value: object) -> object:
    """Drop author locators only after Studio has physically resolved their content."""
    forbidden = {"map_id", "map_snapshot_hash", "revision_id", "brain_revision_id", "brain_revision_hash",
                 "skill_revision_id", "source_revision_id", "secret_ref"}
    if isinstance(value, dict):
        return {key: _portable_content(child) for key, child in value.items()
                if key not in forbidden and not key.endswith("_revision_id")}
    if isinstance(value, list):
        return [_portable_content(child) for child in value]
    return value


class ExperimentPackageBuilder:
    """Build a complete experiment with one physical copy of each resource."""

    def build_directory(self, destination: str | Path, *, definition: object, skills: Sequence[SkillSource],
                        brain_skill: str, object_roots: Sequence[str] = (),
                        asset_sources: Mapping[str, str | Path | bytes] | None = None,
                        experiment_id: str | None = None, created_at: datetime | None = None,
                        metadata: Mapping[str, object] | None = None,
                        resource_set: ResourceSet | None = None,
                        model_selections: dict[str, ResourceRef] | None = None) -> Path:
        destination = checked_package_path(Path(destination))
        if destination.exists():
            raise PackageError(f"experiment destination already exists: {destination}")
        raw = _portable_content(_plain_mapping(definition))
        identity = raw.get("experiment")
        if not isinstance(identity, Mapping):
            raise PackageError("definition.experiment is required")
        raw.setdefault("engine", {})["brain_skill"] = brain_skill
        resources = copy.deepcopy(resource_set) if resource_set is not None else ResourceSet()
        for source in skills:
            skill_file = checked_package_path(Path(source.skill_file))
            if skill_file.name != "SKILL.md" or not skill_file.is_file():
                raise PackageError(f"Skill source must point to SKILL.md: {skill_file}")
            folder = f"skills/items/{source.skill_id}"
            attachments = []
            for relative, path in iter_package_files(skill_file.parent):
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                target = validate_package_path(f"{folder}/{relative}")
                content = path.read_bytes()
                if target in resources.files and resources.files[target] != content:
                    raise PackageError(f"conflicting Skill attachment: {target}")
                resources.files[target] = content
                attachments.append(target)
            record = ResourceRecord(kind="skill", key=source.skill_id, name=source.skill_id,
                definition={"skill_kind": source.editor_kind or ("brain" if source.kind == "brain" else "atomic"),
                            "entrypoint": f"{folder}/SKILL.md"}, attachments=attachments)
            document = skill_document(record, resources)
            record.description = document.description
            record.dependencies = [ResourceRef(kind="skill", key=key) for key in document.children]
            if source.dependencies and set(source.dependencies) != set(document.children):
                raise PackageError(f"Skill {source.skill_id} dependency metadata disagrees with Markdown")
            previous = resources.by_ref().get(record.identity)
            if previous is not None:
                if previous.definition.get("skill_kind") != record.definition["skill_kind"]:
                    raise PackageError(f"conflicting Skill type: {record.key}")
                # Bytes were compared while materializing above. Both inputs
                # describe the same explicit author selection, not revisions.
                resources.resources = [item for item in resources.resources if item.identity != record.identity]
            resources.resources.append(record)
        for logical, source in (asset_sources or {}).items():
            logical = validate_package_path(logical)
            content = source if isinstance(source, bytes) else checked_package_path(Path(source)).read_bytes()
            # The shared ResourceSet may already own a map/Agent attachment
            # also collected while materializing the experiment definition.
            if logical in resources.files and resources.files[logical] != content:
                raise PackageError(f"conflicting resource attachment: {logical}")
            resources.files[logical] = content
        manifest = ExperimentManifest(experiment=ExperimentIdentity(
            experiment_id=experiment_id or str(uuid4()), key=str(identity.get("key") or "experiment"),
            name=str(identity.get("name") or identity.get("key") or "Experiment"),
            goal=str(identity.get("goal") or ""), timezone=str(identity.get("timezone") or "Asia/Shanghai")),
            created_at=created_at or datetime.now(UTC), entrypoints=ExperimentEntrypoints(), metadata=dict(metadata or {}))
        destination.mkdir(parents=True)
        try:
            atomic_write_json(destination / "manifest.json", manifest.model_dump(mode="json"))
            write_experiment_definition(destination, raw, resource_set=resources, model_selections=model_selections)
            write_integrity_manifest(destination)
            validate_experiment_directory(destination)
            return destination
        except Exception:
            # This exact checked directory was created exclusively by this call.
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def build_archive(self, archive: str | Path, *, staging_directory: str | Path, **build_arguments) -> Path:
        archive = checked_package_path(Path(archive))
        if archive.suffix.casefold() != ".gaexp":
            raise PackageError("experiment archives must use the .gaexp suffix")
        directory = self.build_directory(staging_directory, **build_arguments)
        return seal_directory(directory, archive)
