"""Build a self-contained experiment package from mutable Studio resources."""

from __future__ import annotations

import copy
import hashlib
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from generative_agents.ga_protocol import (
    ExperimentEntrypoints,
    ExperimentIdentity,
    ExperimentManifest,
    PackageError,
    SkillPackageEntry,
    SkillPackageRegistry,
    atomic_write_json,
    seal_directory,
    sha256_file,
    validate_experiment_directory,
    write_integrity_manifest,
)
from generative_agents.ga_protocol.constants import DEFAULT_EXPERIMENT_ENTRYPOINTS
from generative_agents.ga_protocol.models import validate_package_path


@dataclass(frozen=True, slots=True)
class SkillSource:
    """One already-resolved Skill in the experiment's physical closure."""

    skill_id: str
    kind: Literal["brain", "sub_skill", "object"]
    skill_file: Path
    dependencies: tuple[str, ...] = ()
    editor_kind: Literal["brain", "pack", "atomic"] | None = None


def _plain_mapping(value: object) -> dict:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise PackageError("experiment definition must be a mapping or Pydantic model")
    return copy.deepcopy(dict(value))


def _remove_legacy_references(value: object) -> object:
    """Remove author-catalog references after their content has been copied."""

    forbidden = {
        "map_id",
        "map_snapshot_hash",
        "revision_id",
        "brain_revision_id",
        "brain_revision_hash",
        "skill_revision_id",
        "source_revision_id",
        "secret_ref",
    }
    if isinstance(value, dict):
        return {
            key: _remove_legacy_references(child)
            for key, child in value.items()
            if key not in forbidden and not key.endswith("_revision_id")
        }
    if isinstance(value, list):
        return [_remove_legacy_references(child) for child in value]
    return value


def _semantic_node_id(kind: str, address: Sequence[str]) -> str:
    digest = hashlib.sha256(
        (kind + "\0" + "\0".join(address)).encode("utf-8")
    ).hexdigest()[:24]
    return f"semantic-{kind.casefold()}-{digest}"


def build_semantic_index(world: dict) -> tuple[dict, dict]:
    """Build one package-owned hierarchy and coordinate lookup.

    Author-provided node IDs are retained.  Maps without an editor hierarchy
    receive deterministic package-local IDs derived from their full address.
    """

    prepared = copy.deepcopy(world)
    definition = prepared.get("definition")
    if not isinstance(definition, dict):
        raise PackageError("definition.world.definition must be an object")
    from generative_agents.config.map_editor import MapEditorDocumentV2
    from generative_agents.ga_protocol.navigation import compile_collision

    if definition.get("editor_v2"):
        definition["editor_v2"] = MapEditorDocumentV2.model_validate(
            definition["editor_v2"]
        ).model_dump(mode="json")
        compile_collision(definition)
    tiles = definition.get("tiles")
    if not isinstance(tiles, list):
        raise PackageError("definition.world.definition.tiles must be an array")
    editor = definition.get("editor_v2")
    raw_nodes = editor.get("hierarchy_nodes") if isinstance(editor, dict) else None
    nodes: dict[str, dict] = {}
    path_to_id: dict[tuple[str, ...], str] = {}

    if isinstance(raw_nodes, list) and raw_nodes:
        source_by_id = {
            str(item.get("id")): item
            for item in raw_nodes
            if isinstance(item, Mapping) and str(item.get("id") or "").strip()
        }
        if len(source_by_id) != len(raw_nodes):
            raise PackageError("semantic hierarchy node IDs must be present and unique")

        def address_for(node: Mapping) -> list[str]:
            parts: list[str] = []
            current: Mapping | None = node
            visited: set[str] = set()
            while current is not None:
                node_id = str(current.get("id") or "")
                if not node_id or node_id in visited:
                    raise PackageError(f"semantic hierarchy contains a cycle at {node_id}")
                visited.add(node_id)
                parts.append(str(current.get("name") or node_id))
                parent_id = str(current.get("parent_id") or "")
                current = source_by_id.get(parent_id) if parent_id else None
            return list(reversed(parts))

        for node_id, source in source_by_id.items():
            address = address_for(source)
            bounds = source.get("bounds") if isinstance(source.get("bounds"), Mapping) else {}
            node = {
                "id": node_id,
                "kind": str(source.get("kind") or "").upper(),
                "name": str(source.get("name") or node_id),
                "semantic": str(source.get("semantic") or ""),
                "parent_id": str(source.get("parent_id") or "") or None,
                "address": address,
                "bounds": {
                    "x": int(bounds.get("x") or 0),
                    "y": int(bounds.get("y") or 0),
                    "width": max(1, int(bounds.get("width") or 1)),
                    "height": max(1, int(bounds.get("height") or 1)),
                },
                "skill_bindings": copy.deepcopy(source.get("skill_bindings") or []),
                "available_visual_states": [case["value"] for case in (source.get("state_appearance") or {}).get("cases", [])],
            }
            nodes[node_id] = node
            path_to_id[tuple(address)] = node_id
    else:
        kind_by_level = ("WORLD", "SECTOR", "ARENA", "GAME_OBJECT")
        extents: dict[str, list[int]] = {}
        for tile in tiles:
            if not isinstance(tile, Mapping):
                continue
            address = tile.get("address")
            coord = tile.get("coord")
            if not isinstance(address, list) or not isinstance(coord, list) or len(coord) != 2:
                continue
            parent_id = None
            for level, name in enumerate(address[:4]):
                path = tuple(str(part) for part in address[: level + 1])
                kind = kind_by_level[level]
                node_id = path_to_id.setdefault(path, _semantic_node_id(kind, path))
                if node_id not in nodes:
                    nodes[node_id] = {
                        "id": node_id,
                        "kind": kind,
                        "name": str(name),
                        "semantic": "",
                        "parent_id": parent_id,
                        "address": list(path),
                        "bounds": {},
                        "skill_bindings": [],
                    }
                    extents[node_id] = [coord[0], coord[1], coord[0], coord[1]]
                else:
                    extent = extents[node_id]
                    extent[0] = min(extent[0], coord[0])
                    extent[1] = min(extent[1], coord[1])
                    extent[2] = max(extent[2], coord[0])
                    extent[3] = max(extent[3], coord[1])
                parent_id = node_id
        for node_id, extent in extents.items():
            x_min, y_min, x_max, y_max = extent
            nodes[node_id]["bounds"] = {
                "x": x_min,
                "y": y_min,
                "width": x_max - x_min + 1,
                "height": y_max - y_min + 1,
            }

    coordinate_paths: dict[str, list[str]] = {}
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        address = tile.get("address")
        coord = tile.get("coord")
        if not isinstance(address, list) or not isinstance(coord, list) or len(coord) != 2:
            continue
        node_ids = [
            path_to_id[tuple(str(part) for part in address[:level])]
            for level in range(1, min(4, len(address)) + 1)
            if tuple(str(part) for part in address[:level]) in path_to_id
        ]
        coordinate_paths[f"{coord[0]},{coord[1]}"] = node_ids
        tile["spatial_semantics"] = [
            {
                "id": nodes[node_id]["id"],
                "kind": nodes[node_id]["kind"],
                "name": nodes[node_id]["name"],
                "semantic": nodes[node_id]["semantic"],
            }
            for node_id in node_ids
        ]

    semantic_index = {
        "schema_version": 1,
        "levels": ["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"],
        "nodes": sorted(
            nodes.values(),
            key=lambda node: (len(node["address"]), node["address"], node["id"]),
        ),
        "coordinate_paths": dict(sorted(coordinate_paths.items())),
    }
    definition["semantic_index"] = copy.deepcopy(semantic_index)
    return prepared, semantic_index


class ExperimentPackageBuilder:
    """Materialize a complete immutable-by-content ``.gaexp`` source tree.

    Resource IDs passed to Studio are resolution inputs only.  Once this method
    returns, the package contains the selected Map, Agent, Brain, recursive Skill,
    model, evaluator, and asset content and has no live catalog dependency.
    """

    def build_directory(
        self,
        destination: str | Path,
        *,
        definition: object,
        skills: Sequence[SkillSource],
        brain_skill: str,
        object_roots: Sequence[str] = (),
        asset_sources: Mapping[str, str | Path | bytes] | None = None,
        experiment_id: str | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Path:
        destination = Path(destination).resolve()
        if destination.exists():
            raise PackageError(f"experiment destination already exists: {destination}")
        raw = _plain_mapping(definition)
        identity = raw.get("experiment")
        if not isinstance(identity, Mapping):
            raise PackageError("definition.experiment is required")
        destination.mkdir(parents=True)
        try:
            self._write_definition(destination, raw)
            registry = self._copy_skills(
                destination,
                skills,
                brain_skill=brain_skill,
                object_roots=object_roots,
            )
            atomic_write_json(
                destination / DEFAULT_EXPERIMENT_ENTRYPOINTS["skills"],
                registry.model_dump(mode="json"),
            )
            self._copy_assets(destination, asset_sources or {})
            manifest = ExperimentManifest(
                experiment=ExperimentIdentity(
                    experiment_id=experiment_id or str(uuid4()),
                    key=str(identity.get("key") or "experiment"),
                    name=str(identity.get("name") or identity.get("key") or "Experiment"),
                    goal=str(identity.get("goal") or ""),
                    timezone=str(identity.get("timezone") or "Asia/Shanghai"),
                ),
                created_at=created_at or datetime.now(UTC),
                entrypoints=ExperimentEntrypoints(**DEFAULT_EXPERIMENT_ENTRYPOINTS),
                metadata=dict(metadata or {}),
            )
            atomic_write_json(destination / "manifest.json", manifest.model_dump(mode="json"))
            write_integrity_manifest(destination)
            validate_experiment_directory(destination)
            return destination
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def build_archive(
        self,
        archive: str | Path,
        *,
        staging_directory: str | Path,
        **build_arguments,
    ) -> Path:
        """Build a directory and seal the byte-equivalent ``.gaexp`` archive."""

        archive = Path(archive).resolve()
        if archive.suffix.casefold() != ".gaexp":
            raise PackageError("experiment archives must use the .gaexp suffix")
        directory = self.build_directory(staging_directory, **build_arguments)
        return seal_directory(directory, archive)

    @staticmethod
    def _write_definition(root: Path, raw: dict, *, reuse_world: bool = False) -> None:
        # Cleaning below constructs new containers; do not deep-copy the large map first.
        prepared = dict(raw)
        if reuse_world:
            prepared.pop('world', None)
        raw_models = prepared.get("models")
        if isinstance(raw_models, Mapping):
            raw_models = dict(raw_models)
            for purpose, default_env in (
                ("chat", "GA_CHAT_API_KEY"),
                ("embedding", "GA_EMBEDDING_API_KEY"),
            ):
                transport = raw_models.get(purpose)
                if not isinstance(transport, Mapping):
                    continue
                transport = dict(transport)
                had_studio_secret = bool(transport.pop("secret_ref", None))
                if transport.get("provider") == "openai" or had_studio_secret:
                    transport.setdefault("credential_env", default_env)
                raw_models[purpose] = transport
            prepared["models"] = raw_models
        cleaned = _remove_legacy_references(prepared)
        world = raw.get("world") if reuse_world else cleaned.get("world")
        agents = cleaned.get("agents")
        models = cleaned.get("models")
        simulation = cleaned.get("simulation")
        engine = cleaned.get("engine") or {}
        if not isinstance(world, Mapping):
            raise PackageError("definition.world must contain the copied Map")
        if not isinstance(agents, list):
            raise PackageError("definition.agents must contain copied Agent definitions")
        if not isinstance(models, Mapping):
            raise PackageError("definition.models is required")
        if not isinstance(simulation, Mapping):
            raise PackageError("definition.simulation is required")
        if not isinstance(engine, Mapping):
            raise PackageError("definition.engine must be an object")
        payloads = {
            "agents": {"schema_version": 1, "agents": agents, "crowds": list(cleaned.get("crowds") or [])},
            "models": {"schema_version": 1, **dict(models)},
            "simulation": {"schema_version": 1, **dict(simulation)},
            "engine": {"schema_version": 1, **dict(engine)},
            "evaluation": {
                "schema_version": 1,
                "results": dict(cleaned.get("results") or {}),
                "evaluators": list(
                    (cleaned.get("evaluation") or {}).get("evaluators") or []
                )
                if isinstance(cleaned.get("evaluation") or {}, Mapping)
                else [],
            },
        }
        if not reuse_world:
            indexed_world, semantic_index = build_semantic_index(dict(world))
            payloads['world'] = {'schema_version': 1, **indexed_world}
            payloads['semantic_index'] = semantic_index
        for name, payload in payloads.items():
            atomic_write_json(root / DEFAULT_EXPERIMENT_ENTRYPOINTS[name], payload)

    @staticmethod
    def _copy_skills(
        root: Path,
        skills: Sequence[SkillSource],
        *,
        brain_skill: str,
        object_roots: Sequence[str],
    ) -> SkillPackageRegistry:
        if not skills:
            raise PackageError("an experiment must physically contain its Brain Skill closure")
        seen: set[str] = set()
        entries: list[SkillPackageEntry] = []
        for index, source in enumerate(sorted(skills, key=lambda item: item.skill_id), start=1):
            if source.skill_id in seen:
                raise PackageError(f"duplicate Skill ID: {source.skill_id}")
            seen.add(source.skill_id)
            skill_file = Path(source.skill_file).resolve()
            if skill_file.name.casefold() != "skill.md" or not skill_file.is_file():
                raise PackageError(f"Skill source must point to SKILL.md: {skill_file}")
            package_dir = Path("skills") / "items" / f"s{index:04d}"
            target_dir = root / package_dir
            target_dir.mkdir(parents=True)
            for path in sorted(skill_file.parent.rglob("*")):
                if path.is_symlink():
                    raise PackageError(f"Skill bundles cannot contain links: {path}")
                if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                relative = path.relative_to(skill_file.parent)
                target = target_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            target_skill = target_dir / "SKILL.md"
            entries.append(
                SkillPackageEntry(
                    skill_id=source.skill_id,
                    kind=source.kind,
                    editor_kind=source.editor_kind,
                    path=target_skill.relative_to(root).as_posix(),
                    dependencies=list(source.dependencies),
                    content_sha256=sha256_file(target_skill),
                )
            )
        return SkillPackageRegistry(
            brain_skill=brain_skill,
            object_roots=list(object_roots),
            skills=entries,
        )

    @staticmethod
    def _copy_assets(root: Path, sources: Mapping[str, str | Path | bytes]) -> None:
        for logical_path, raw_source in sorted(sources.items()):
            relative = validate_package_path(logical_path)
            if relative in {"manifest.json", "integrity/sha256.json"}:
                raise PackageError(f"asset path collides with protocol metadata: {relative}")
            target = root / relative
            if target.exists():
                raise PackageError(f"asset path collides with package content: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(raw_source, bytes):
                target.write_bytes(raw_source)
                continue
            source = Path(raw_source).resolve()
            if source.is_symlink() or not source.is_file():
                raise PackageError(f"asset source is not a regular file: {source}")
            shutil.copyfile(source, target)
