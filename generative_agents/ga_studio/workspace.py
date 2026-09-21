"""Create and edit experiments by physically copying current Studio content."""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import select

from generative_agents.assets import AssetStore
from generative_agents.config.schema import ModelsConfig, SimulationConfig
from generative_agents.ga_protocol import (
    PackageError,
    atomic_write_bytes,
    atomic_write_json,
    open_package,
    read_json,
    validate_experiment_directory,
    write_integrity_manifest,
)
from generative_agents.persistence.models import (
    Asset,
    StudioAgent,
    StudioCrowd,
    StudioEvaluator,
    StudioModelPreset,
    StudioSkill,
    WorldMap,
)
from generative_agents.services.maps import WorldMapService
from generative_agents.skills import DatabaseSkillRegistry

from .builder import ExperimentPackageBuilder, SkillSource, build_semantic_index
from .catalog import StudioPackageCatalogService
from .resources import StudioAgentDefinition, StudioResourceError
from generative_agents.ga_protocol.locking import package_lock


class WorkspaceConflictError(PackageError):
    pass


def serialized_workspace(operation):
    @wraps(operation)
    def wrapped(self, experiment_id, *args, **kwargs):
        row = self.catalog.get('experiment', experiment_id)
        if row is None:
            raise PackageError(f'experiment is not in the Studio package catalog: {experiment_id}')
        with package_lock(self.package_root / 'experiments' / f'{experiment_id}.identity'):
            row = self.catalog.get('experiment', experiment_id)
            if row is None:
                raise PackageError(f'experiment is not in the Studio package catalog: {experiment_id}')
            with package_lock(Path(row.location)):
                return operation(self, experiment_id, *args, **kwargs)
    return wrapped


@dataclass(frozen=True, slots=True)
class AgentPlacement:
    """Experiment-owned placement for one selected public Agent."""

    agent_id: str
    coord: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ExperimentSelection:
    """Studio IDs used only during one physical copy operation."""

    name: str
    goal: str
    map_id: str
    brain_skill_id: str
    model_preset_id: str
    embedding_model_preset_id: str | None = None
    agent_ids: tuple[str, ...] = ()
    crowd_ids: tuple[str, ...] = ()
    evaluator_ids: tuple[str, ...] = ()
    placements: tuple[AgentPlacement, ...] = ()
    key: str | None = None
    timezone: str = "Asia/Shanghai"
    simulation: Mapping[str, Any] | None = None


class ExperimentWorkspaceService:
    """Resolve current author rows once, then make the package authoritative."""

    def __init__(
        self,
        database,
        *,
        package_root: str | Path,
        var_dir: str | Path,
        skill_registry: DatabaseSkillRegistry | None = None,
    ) -> None:
        self.database = database
        self.package_root = Path(package_root).resolve()
        self.package_root.mkdir(parents=True, exist_ok=True)
        self.asset_store = AssetStore(var_dir)
        self.skill_registry = skill_registry or DatabaseSkillRegistry(
            database,
            cache_root=Path(var_dir) / "skill-runtime-cache",
        )
        self.map_service = WorldMapService(database, skill_registry=self.skill_registry)
        self.catalog = StudioPackageCatalogService(database)
        self.builder = ExperimentPackageBuilder()

    def create(self, selection: ExperimentSelection) -> dict[str, Any]:
        experiment_id = str(uuid4())
        workspace = self.package_root / "experiments" / f"workspace-{uuid4().hex}"
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, selection.map_id)
            self._available(public_map, "Map", selection.map_id)
            world = self.map_service.materialize_validated_world(
                session,
                public_map.id,
            ).model_dump(mode="json", exclude_none=False)

            selected_agent_ids = self._expand_agents(
                session,
                agent_ids=selection.agent_ids,
                crowd_ids=selection.crowd_ids,
            )
            crowds = []
            for crowd_id in dict.fromkeys(selection.crowd_ids):
                crowd = session.get(StudioCrowd, crowd_id)
                crowds.append({
                    "crowd_key": crowd.crowd_key, "name": crowd.name,
                    "description": crowd.description or "",
                    "agent_keys": [session.get(StudioAgent, key).agent_key for key in crowd.agent_ids_json],
                })
            agents, agent_assets = self._materialize_agents(
                session,
                selected_agent_ids,
                world=world,
                placements=selection.placements,
            )
            model_preset = session.get(StudioModelPreset, selection.model_preset_id)
            self._available(model_preset, "Model preset", selection.model_preset_id)
            model_config = copy.deepcopy(model_preset.config_json)
            if selection.embedding_model_preset_id:
                embedding_preset = session.get(StudioModelPreset, selection.embedding_model_preset_id)
                self._available(embedding_preset, "Embedding model", selection.embedding_model_preset_id)
                model_config['embedding'] = copy.deepcopy(embedding_preset.config_json.get('embedding'))
            models = ModelsConfig.model_validate(model_config).model_dump(
                mode="json", exclude_none=False
            )
            evaluators = []
            for evaluator_id in selection.evaluator_ids:
                evaluator = session.get(StudioEvaluator, evaluator_id)
                self._available(evaluator, "Evaluator", evaluator_id)
                evaluators.append(copy.deepcopy(evaluator.config_json))
            world_assets = self._world_assets(session, world)
            self._replace_uploaded_world_asset_references(world)
            brain = session.get(StudioSkill, selection.brain_skill_id)
            self._available(brain, "Brain Skill", selection.brain_skill_id)
            if brain.kind != "brain":
                raise StudioResourceError(f"Selected Skill is not a Brain: {brain.skill_key}")
            object_roots = tuple(sorted(self._object_skill_names(world)))
            skill_records = self._skill_records(
                session,
                brain_name=brain.skill_key,
                object_roots=object_roots,
            )
            brain_name = brain.skill_key
        simulation = SimulationConfig.model_validate(
            selection.simulation or self._default_simulation()
        ).model_dump(mode="json", exclude_none=False)
        definition = {
            "schema_version": 1,
            "experiment": {
                "key": self._experiment_key(selection.key or selection.name),
                "name": selection.name.strip(),
                "goal": selection.goal,
                "timezone": selection.timezone,
            },
            "engine": {
                "algorithm_version": "ga-cn-v1",
                "brain_skill": brain_name,
            },
            "simulation": simulation,
            "results": {
                "agent_step_projection_interval_steps": 1,
                "capture_model_payloads": False,
            },
            "models": models,
            "world": world,
            "agents": agents,
            "crowds": crowds,
            "evaluation": {"evaluators": evaluators},
        }
        with tempfile.TemporaryDirectory(prefix="ga-studio-skills-") as temporary:
            skills = self._skill_sources(
                skill_records,
                Path(temporary),
                object_roots=object_roots,
            )
            self.builder.build_directory(
                workspace,
                definition=definition,
                skills=skills,
                brain_skill=brain_name,
                object_roots=object_roots,
                asset_sources={**world_assets, **agent_assets},
                experiment_id=experiment_id,
                metadata={"created_by": "ga_studio"},
            )
        record = self.catalog.upsert(workspace)
        return {
            "experiment_id": experiment_id,
            "location": str(workspace),
            "content_sha256": record.content_sha256,
            "name": record.display_name,
        }

    @serialized_workspace
    def update_entrypoint(
        self,
        experiment_id: str,
        section: str,
        document: Mapping[str, Any],
        *, expected_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Edit one package-owned JSON document and refresh package integrity."""

        row = self.catalog.get("experiment", experiment_id)
        if row is None:
            raise PackageError(f"experiment is not in the Studio package catalog: {experiment_id}")
        root = Path(row.location).resolve()
        if not root.is_dir():
            raise PackageError("sealed .gaexp files are read-only; materialize a workspace first")
        manifest = validate_experiment_directory(root)
        entrypoints = manifest.entrypoints.model_dump(exclude_none=True)
        if section not in entrypoints:
            raise PackageError(f"unknown experiment section: {section}")
        target = root / entrypoints[section]
        original = target.read_bytes()
        semantic_target = root / entrypoints["semantic_index"]
        original_semantic = semantic_target.read_bytes()
        integrity_path = root / "integrity" / "sha256.json"
        if expected_content_sha256 and read_json(integrity_path)['root_sha256'] != expected_content_sha256:
            raise WorkspaceConflictError('实验已被其他保存操作修改，请重新打开实验后再保存；本次修改未覆盖已有内容。')
        original_integrity = integrity_path.read_bytes()
        try:
            payload = copy.deepcopy(dict(document))
            payload["schema_version"] = 1
            if section == "world":
                payload, semantic_index = build_semantic_index(payload)
                payload["schema_version"] = 1
                atomic_write_json(semantic_target, semantic_index)
            atomic_write_json(target, payload)
            write_integrity_manifest(root)
            validate_experiment_directory(root)
        except Exception:
            atomic_write_bytes(target, original)
            atomic_write_bytes(semantic_target, original_semantic)
            atomic_write_bytes(integrity_path, original_integrity)
            raise
        record = self.catalog.upsert(root)
        return {
            "experiment_id": experiment_id,
            "section": section,
            "content_sha256": record.content_sha256,
        }

    @serialized_workspace
    def replace_definition(
        self,
        experiment_id: str,
        definition: Mapping[str, Any],
        *, expected_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace editable package entrypoints from the Web form."""

        row = self.catalog.get("experiment", experiment_id)
        if row is None:
            raise PackageError(f"experiment is not in the Studio package catalog: {experiment_id}")
        root = Path(row.location).resolve()
        if not root.is_dir():
            raise PackageError("sealed .gaexp files are read-only; duplicate the experiment to edit it")
        manifest = validate_experiment_directory(root)
        manifest_document = read_json(root / "manifest.json")
        if expected_content_sha256 and read_json(root / 'integrity/sha256.json')['root_sha256'] != expected_content_sha256:
            raise WorkspaceConflictError('实验已被其他保存操作修改，请重新打开实验后再保存；本次修改未覆盖已有内容。')
        if not isinstance(manifest_document, dict):
            raise PackageError("experiment manifest is invalid")
        entrypoints = manifest.entrypoints.model_dump(exclude_none=True)
        tracked = {
            root / "manifest.json",
            root / "integrity" / "sha256.json",
            *(root / relative for relative in entrypoints.values()),
        }
        originals = {path: path.read_bytes() for path in tracked if path.is_file()}
        try:
            payload = dict(definition)
            identity = payload.get("experiment")
            if not isinstance(identity, Mapping):
                raise PackageError("definition.experiment is required")
            saved_world = read_json(root / entrypoints['world'])
            saved_world.pop('schema_version', None)
            self.builder._write_definition(root, payload, reuse_world=payload.get('world') == saved_world)
            manifest_document["experiment"] = {
                "experiment_id": experiment_id,
                "key": str(identity.get("key") or manifest.experiment.key),
                "name": str(identity.get("name") or manifest.experiment.name),
                "goal": str(identity.get("goal") or ""),
                "timezone": str(identity.get("timezone") or manifest.experiment.timezone),
            }
            atomic_write_json(root / "manifest.json", manifest_document)
            write_integrity_manifest(root)
            validated = validate_experiment_directory(root)
        except Exception:
            for path, content in originals.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(path, content)
            raise
        record = self.catalog.record_validated_experiment(
            root, validated, read_json(root / 'integrity/sha256.json')['root_sha256'],
        )
        return {
            "experiment_id": experiment_id,
            "content_sha256": record.content_sha256,
            "definition": dict(definition),
        }

    @serialized_workspace
    def duplicate(self, experiment_id: str) -> dict[str, Any]:
        """Copy a package into a new independent DRAFT experiment workspace."""

        row = self.catalog.get("experiment", experiment_id)
        if row is None:
            raise PackageError(f"experiment is not in the Studio package catalog: {experiment_id}")
        new_id = str(uuid4())
        destination = self.package_root / "experiments" / f"workspace-{uuid4().hex}"
        try:
            with open_package(Path(row.location)) as source:
                shutil.copytree(source, destination)
            integrity = destination / "integrity" / "sha256.json"
            integrity.unlink(missing_ok=True)
            manifest = read_json(destination / "manifest.json")
            if not isinstance(manifest, dict) or not isinstance(manifest.get("experiment"), dict):
                raise PackageError("experiment manifest is invalid")
            identity = dict(manifest["experiment"])
            identity["experiment_id"] = new_id
            identity["name"] = f"{identity.get('name') or row.display_name} 副本"
            identity["key"] = self._experiment_key(f"{identity.get('key') or 'experiment'}-copy-{new_id[:8]}")
            manifest["experiment"] = identity
            atomic_write_json(destination / "manifest.json", manifest)
            write_integrity_manifest(destination)
            validate_experiment_directory(destination)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            raise
        record = self.catalog.upsert(destination)
        return {
            "experiment_id": new_id,
            "location": str(destination),
            "content_sha256": record.content_sha256,
            "name": record.display_name,
        }

    @serialized_workspace
    def add_assets(
        self,
        experiment_id: str,
        assets: Mapping[str, bytes],
        *, expected_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Copy uploaded bytes into an editable experiment package immediately."""

        row = self.catalog.get("experiment", experiment_id)
        if row is None:
            raise PackageError(f"experiment is not in the Studio package catalog: {experiment_id}")
        root = Path(row.location).resolve()
        if not root.is_dir():
            raise PackageError("sealed .gaexp files are read-only; duplicate the experiment to edit it")
        validate_experiment_directory(root)
        integrity_path = root / "integrity" / "sha256.json"
        if expected_content_sha256 and read_json(integrity_path)['root_sha256'] != expected_content_sha256:
            raise WorkspaceConflictError('实验已被其他保存操作修改，请重新打开实验后再保存；本次修改未覆盖已有内容。')
        original_integrity = integrity_path.read_bytes()
        created: list[Path] = []
        try:
            for logical_path, content in assets.items():
                relative = Path(str(logical_path).replace("\\", "/"))
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not relative.parts
                    or relative.parts[0] != "assets"
                ):
                    raise PackageError(f"unsafe experiment asset path: {logical_path}")
                target = (root / relative).resolve()
                target.relative_to(root)
                if target.exists():
                    raise PackageError(f"experiment asset already exists: {logical_path}")
                atomic_write_bytes(target, bytes(content))
                created.append(target)
            write_integrity_manifest(root)
            validate_experiment_directory(root)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            atomic_write_bytes(integrity_path, original_integrity)
            raise
        record = self.catalog.upsert(root)
        return {
            "experiment_id": experiment_id,
            "content_sha256": record.content_sha256,
            "assets": list(assets),
        }

    @classmethod
    def _skill_records(cls, session, *, brain_name: str, object_roots: Sequence[str]):
        records = {}
        pending = list(reversed([brain_name, *object_roots]))
        while pending:
            name = pending.pop()
            if name in records:
                continue
            row = session.scalar(select(StudioSkill).where(StudioSkill.skill_key == name))
            cls._available(row, "Skill", name)
            records[name] = {
                "name": row.skill_key,
                "kind": row.kind,
                "markdown": row.markdown,
                "scripts": copy.deepcopy(row.scripts_json or {}),
                "dependencies": tuple(row.children_json or ()),
            }
            pending.extend(reversed(row.children_json or ()))
        return records

    @staticmethod
    def _skill_sources(
        records,
        root: Path,
        *,
        object_roots: Sequence[str],
    ) -> list[SkillSource]:
        result = []
        brain_names = {name for name, record in records.items() if record["kind"] == "brain"}
        if len(brain_names) != 1:
            raise PackageError(f"experiment Skill closure must contain one Brain: {sorted(brain_names)}")
        brain_name = next(iter(brain_names))
        object_skills = set(object_roots)
        for name, record in sorted(records.items()):
            folder = root / name
            folder.mkdir(parents=True)
            skill_file = folder / "SKILL.md"
            skill_file.write_text(record["markdown"], encoding="utf-8")
            for raw_relative, source in sorted(record["scripts"].items()):
                relative = Path(str(raw_relative).replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "scripts":
                    raise PackageError(f"unsafe Skill private file: {raw_relative}")
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            result.append(
                SkillSource(
                    skill_id=name,
                    kind=(
                        "brain"
                        if name == brain_name
                        else "object"
                        if name in object_skills
                        else "sub_skill"
                    ),
                    skill_file=skill_file,
                    dependencies=record["dependencies"],
                    editor_kind=record["kind"],
                )
            )
        return result

    @staticmethod
    def _object_skill_names(world: object) -> set[str]:
        result: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                bindings = value.get("skill_bindings")
                if isinstance(bindings, list):
                    for binding in bindings:
                        if isinstance(binding, dict) and str(binding.get("skill_name") or "").strip():
                            result.add(str(binding["skill_name"]).strip())
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(world)
        return result

    @classmethod
    def _expand_agents(cls, session, *, agent_ids, crowd_ids) -> list[str]:
        selected = list(agent_ids)
        for crowd_id in crowd_ids:
            crowd = session.get(StudioCrowd, crowd_id)
            cls._available(crowd, "Crowd", crowd_id)
            selected.extend(crowd.agent_ids_json or [])
        result = list(dict.fromkeys(selected))
        if not result:
            raise StudioResourceError("At least one Agent or Crowd must be selected")
        return result

    def _materialize_agents(self, session, agent_ids, *, world, placements):
        tiles = self._walkable_tiles(world)
        placement_by_agent = {item.agent_id: item.coord for item in placements}
        unknown = sorted(set(placement_by_agent) - set(agent_ids))
        if unknown:
            raise StudioResourceError(f"placements refer to unselected Agents: {unknown}")
        tile_by_coord = {tuple(tile["coord"]): tile for tile in tiles}
        free = [tuple(tile["coord"]) for tile in tiles]
        occupied: set[tuple[int, int]] = set()
        agents = []
        assets: dict[str, str | Path | bytes] = {}
        seen_keys: set[str] = set()
        for agent_id in agent_ids:
            row = session.get(StudioAgent, agent_id)
            self._available(row, "Agent", agent_id)
            source = StudioAgentDefinition.model_validate(row.definition_json)
            if source.agent_key in seen_keys:
                raise StudioResourceError(f"duplicate Agent key in selection: {source.agent_key}")
            seen_keys.add(source.agent_key)
            coord = placement_by_agent.get(agent_id)
            if coord is None:
                coord = next((candidate for candidate in free if candidate not in occupied), None)
            if coord is None or coord in occupied or coord not in tile_by_coord:
                raise StudioResourceError(f"Agent placement is unavailable: {source.agent_key} at {coord}")
            occupied.add(coord)
            address = list(tile_by_coord[coord]["address"])
            payload = source.model_dump(mode="json", exclude_none=False)
            portrait_id = payload.pop("portrait_asset_id", None)
            sprite_id = payload.pop("sprite_asset_id", None)
            payload["coord"] = list(coord)
            payload["spatial"] = {
                "address": {"initial_location": address},
                "tree": self._spatial_tree(address),
            }
            payload["portrait_asset"] = self._agent_asset(
                session, portrait_id, source.agent_key, "portrait", assets
            )
            payload["sprite_asset"] = self._agent_asset(
                session, sprite_id, source.agent_key, "sprite", assets
            )
            agents.append(payload)
        return agents, assets

    @staticmethod
    def _walkable_tiles(world: Mapping[str, Any]) -> list[dict[str, Any]]:
        definition = world.get("definition") or {}
        result = []
        for tile in definition.get("tiles") or []:
            if not isinstance(tile, dict) or tile.get("collision") is True:
                continue
            coord = tile.get("coord")
            address = tile.get("address")
            if (
                isinstance(coord, list)
                and len(coord) == 2
                and all(isinstance(value, int) and value >= 0 for value in coord)
                and isinstance(address, list)
                and len(address) in (3, 4)
                and all(isinstance(value, str) and value.strip() for value in address)
            ):
                result.append(tile)
        result.sort(key=lambda tile: (tile["coord"][1], tile["coord"][0]))
        if not result:
            raise StudioResourceError(
                "Map has no walkable Tile with a World/Sector/Arena or Game Object address"
            )
        return result

    @staticmethod
    def _spatial_tree(address: list[str]) -> dict[str, Any]:
        if len(address) not in (3, 4):
            raise StudioResourceError("Agent placement requires an Arena or Game Object semantic address")
        return {address[0]: {address[1]: {address[2]: address[3:]}}}

    def _agent_asset(self, session, asset_id, agent_key, role, targets):
        if not asset_id:
            return None
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise StudioResourceError(f"Agent {role} asset does not exist: {asset_id}")
        extension = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "application/json": ".json",
        }.get(asset.media_type, "")
        logical = f"assets/agents/{agent_key}/{role}{extension}"
        targets[logical] = self._asset_source(asset)
        return logical

    def _world_assets(self, session, world):
        # Uploaded editor sources are valid author references even when the map
        # has no package manifest yet. Build that manifest during physical import.
        references = world.setdefault("assets", [])
        by_hash = {
            str(reference.get("asset_hash") or "").removeprefix("sha256:"): reference
            for reference in references
        }
        by_path = {reference["logical_path"]: reference["asset_hash"] for reference in references}
        sources = ((world.get("definition") or {}).get("editor_v2") or {}).get("material_sources") or []
        for source in sources:
            if source.get("kind") != "UPLOADED":
                continue
            label = source.get("name") or source.get("id")
            asset = session.get(Asset, source.get("asset_id"))
            if asset is None:
                raise StudioResourceError(f"地图上传素材不存在：{label}（{source.get('asset_id')}）")
            digest = str(source.get("asset_hash") or "").removeprefix("sha256:")
            if asset.sha256 != digest:
                raise StudioResourceError(f"地图上传素材哈希不匹配：{label}")
            if digest in by_hash:
                continue
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(asset.media_type, ".bin")
            logical = f"assets/maps/{digest}{suffix}"
            if logical in by_path and by_path[logical] != f"sha256:{digest}":
                raise StudioResourceError(f"地图包内素材路径冲突：{logical}")
            reference = {"logical_path": logical, "asset_hash": f"sha256:{digest}",
                         "media_type": asset.media_type, "size": asset.size_bytes}
            references.append(reference)
            by_hash[digest] = reference
            by_path[logical] = reference["asset_hash"]
        result: dict[str, str | Path | bytes] = {}
        for reference in world.get("assets") or []:
            if not isinstance(reference, dict):
                continue
            digest = str(reference.get("asset_hash") or "").removeprefix("sha256:")
            logical = str(reference.get("logical_path") or "")
            asset = session.scalar(select(Asset).where(Asset.sha256 == digest))
            if asset is None:
                raise StudioResourceError(f"Map asset bytes are unavailable: {logical} ({digest})")
            content = self._asset_source(asset)
            raw = content if isinstance(content, bytes) else Path(content).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest or len(raw) != reference["size"]:
                raise StudioResourceError(f"地图素材内容校验失败：{logical}")
            if logical in result and result[logical] != raw:
                raise StudioResourceError(f"地图包内素材路径冲突：{logical}")
            result[logical] = raw
        return result

    @staticmethod
    def _replace_uploaded_world_asset_references(world: dict[str, Any]) -> None:
        """Turn Studio upload references into package-local asset paths.

        ``asset_id`` is an author-workspace locator.  It must not survive the
        one-time import into an experiment, because Runtime and Replay can only
        resolve bytes that physically exist inside the package.
        """

        logical_by_hash = {
            str(reference.get("asset_hash") or "").removeprefix("sha256:"): str(
                reference.get("logical_path") or ""
            )
            for reference in world.get("assets") or []
            if isinstance(reference, dict)
        }
        definition = world.get("definition")
        editor = definition.get("editor_v2") if isinstance(definition, dict) else None
        sources = editor.get("material_sources") if isinstance(editor, dict) else None
        if not isinstance(sources, list):
            return
        for source in sources:
            if not isinstance(source, dict) or source.get("kind") != "UPLOADED":
                continue
            digest = str(source.get("asset_hash") or "")
            logical_path = logical_by_hash.get(digest)
            if not logical_path:
                raise StudioResourceError(
                    f"Map upload is missing its package asset entry: {source.get('name') or digest}"
                )
            source["kind"] = "BUNDLED"
            source["bundled_path"] = logical_path
            source.pop("asset_id", None)

    def _asset_source(self, asset: Asset):
        if asset.content_blob is not None:
            return bytes(asset.content_blob)
        return self.asset_store.resolve(asset.relative_path, expected_sha256=asset.sha256)

    @staticmethod
    def _available(row, kind: str, resource_id: str) -> None:
        if row is None:
            raise StudioResourceError(f"{kind} does not exist: {resource_id}")
        if getattr(row, "archived_at", None) is not None:
            raise StudioResourceError(f"{kind} is archived: {resource_id}")

    @staticmethod
    def _experiment_key(value: str) -> str:
        key = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
        key = key[:54].strip("-") or "experiment"
        if len(key) < 2:
            key = f"experiment-{key}"
        return key

    @staticmethod
    def _default_simulation() -> dict[str, Any]:
        return {
            "start_time": "2026-01-01T08:00:00+08:00",
            "stride_minutes": 10,
            "max_steps": 1000,
            "checkpoint_interval_steps": 1,
            "checkpoint_retention": 2,
            "random_seed": 42,
            "log_level": "INFO",
        }


__all__ = [
    "AgentPlacement",
    "ExperimentSelection",
    "ExperimentWorkspaceService",
]
