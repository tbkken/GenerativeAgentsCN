"""The shared resource editors' file-backed adapter for one experiment.

Only the catalog resolves an experiment ID. Resource reads and writes below
never resolve public author IDs; all relationships use keys inside the package.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from generative_agents.ga_protocol.schemas.errors import ServiceError

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import atomic_write_bytes
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_integrity
from generative_agents.ga_protocol.packages.reading import open_readonly_package, validated_experiment
from generative_agents.ga_protocol.packages.io import write_integrity_manifest
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.schemas.manifests import validate_package_path
from generative_agents.ga_protocol.schemas.spatial_assets import SpatialAssetContract
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.skills.dependencies import referenced_mcp_tools
from generative_agents.ga_studio.experiments.builder import build_semantic_index
from generative_agents.ga_studio.experiments.workspace import WorkspaceConflictError
from generative_agents.ga_studio.resources.trials import prepare_copied_trial
from generative_agents.ga_studio.storage.credentials import HostModelCredentials
from generative_agents.ga_protocol.packages.definition import (
    _experiment_definition, assemble_skill_registry, read_experiment_assembly, read_experiment_documents, write_experiment_definition,
)
from generative_agents.ga_protocol.packages.resources import read_experiment_resource_set
from generative_agents.ga_protocol.schemas.resources import ResourceIndex, ResourceRecord, ResourceRef
from generative_agents.ga_protocol.packages.resources import ResourceSet
from generative_agents.ga_studio.experiments.transaction import edit_experiment


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def editor_world(world):
    """Project package geometry into an editable view without persisting UI metadata."""
    world = copy.deepcopy(world)
    definition = world.get("definition", {})
    document = definition.get("editor_v2")
    if document:
        height, width = definition["size"]
        document["import_metadata"] = {"width": width, "height": height,
                                       "tile_size": definition.get("tile_size", 32)}
        document["ui_state"] = {}
    return world


class ExperimentResourceError(ServiceError):
    def __init__(self, status, message):
        super().__init__("EXPERIMENT_RESOURCE_ERROR", message, status_code=status)


class ExperimentResourceEditor:
    def __init__(self, workspace):
        self.workspace = workspace

    @contextmanager
    def package(self, experiment_id, *, write=False, expected=None, validate_execution=False):
        with package_lock(self.workspace.package_root / "experiments" / f"{experiment_id}.identity"):
            row = self.workspace.catalog.get("experiment", experiment_id)
            if row is None:
                raise ExperimentResourceError(404, "实验不存在")
            location = Path(row.location)
            if write and not location.is_dir():
                raise ExperimentResourceError(409, "实验已封存；请复制为新的独立实验后修改")
            opener = open_package if write else open_readonly_package
            with package_lock(location), opener(location) as root:
                validate = validate_experiment_directory if write or validate_execution else validated_experiment
                manifest = validate(root)
                digest = read_json(root / "integrity/sha256.json")["root_sha256"]
                if write and expected != digest:
                    raise WorkspaceConflictError("实验内容已变化，请重新打开当前编辑页后保存；本次未覆盖已有内容。")
                yield root, manifest.entrypoints.model_dump(exclude_none=True), digest, location.is_dir()

    def commit(self, root, changes, definition=None, resources=None):
        """Publish the common resources and assembly as one validated draft edit."""
        def update(staged):
            for relative, content in changes.items():
                target = staged / validate_package_path(relative)
                if content is None:
                    target.unlink(missing_ok=True)
                    if resources is not None:
                        resources.files.pop(relative, None)
                else:
                    atomic_write_bytes(target, content)
                    if resources is not None:
                        resources.files[relative] = content
            if resources is None:
                return  # An uploaded attachment is adopted when its resource is saved.
            for record in resources.resources:
                if record.kind == "skill":
                    folder = str(Path(record.definition["entrypoint"]).parent.as_posix()) + "/"
                    record.attachments = sorted(path for path in resources.files if path.startswith(folder))
            write_experiment_definition(staged, definition, resource_set=resources)
        manifest, digest = edit_experiment(root, update)
        self.workspace.catalog.record_validated_experiment(root, manifest, digest)
        return digest

    @staticmethod
    def registry(root):
        _manifest, index, assembly = read_experiment_documents(root)
        return assemble_skill_registry(ResourceSet(index.resources, {}, index.roots),
                                       assembly).model_dump(mode="json")

    @staticmethod
    def skill(root, entry, *, markdown=None, scripts=None):
        if markdown is None and scripts is None:
            source = root / validate_package_path(entry["path"])
            document = SkillRegistry(root=root)._read(source, entry.get("editor_kind") or
                                                      ("brain" if entry["kind"] == "brain" else "atomic"))
            return {**document.detail(), "path": entry["path"], "storage": "experiment",
                    "script_sources": {relative: (source.parent / relative).read_text(encoding="utf-8")
                                       for relative in document.scripts}}
        # The shared parser checks folder/key consistency. Use a temporary local
        # directory named after the package key; never materialize Studio content.
        with tempfile.TemporaryDirectory(prefix="ga-edit-skill-") as temporary:
            folder = Path(temporary) / SkillRegistry.normalize_name(entry["skill_id"])
            source = root / validate_package_path(entry["path"])
            source.resolve().relative_to(root.resolve())
            shutil.copytree(source.parent, folder)
            path = folder / "SKILL.md"
            if markdown is not None:
                path.write_text(markdown, encoding="utf-8")
            if scripts is not None:
                for old in (folder / "scripts").rglob("*"):
                    if old.is_file():
                        old.unlink()
                for relative, source in scripts.items():
                    relative = validate_package_path(relative)
                    if not relative.startswith("scripts/"):
                        raise PackageError("私有脚本必须位于 scripts/ 中")
                    target = folder / relative
                    target.resolve().relative_to(folder.resolve())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source, encoding="utf-8")
            document = SkillRegistry(root=temporary)._read(path, entry.get("editor_kind") or ("brain" if entry["kind"] == "brain" else "atomic"))
            detail = document.detail()
            detail.update(path=entry["path"], storage="experiment", script_sources={
                relative: (folder / relative).read_text(encoding="utf-8") for relative in document.scripts
            })
            return detail

    @staticmethod
    def map_detail(world, digest, editable, experiment_id, *, validated=False):
        world = editor_world(world)
        world.pop("schema_version", None)
        return dict(id=experiment_id, map_key=world.get("world_key", "world"),
                    name=world.get("world_name") or "实验地图", world=world,
                    row_version=digest, editable=editable,
                    validation={"valid": True, "errors": [], "warnings": []} if validated else None)

    def read(self, experiment_id, resource, query, *, validate_execution=False):
        from generative_agents.ga_studio.resources.listing import bounds, page_result
        with self.package(experiment_id, validate_execution=validate_execution) as (root, ep, digest, editable):
            parts = resource.strip("/").split("/")
            family, key = parts[0], parts[1] if len(parts) > 1 else None
            _manifest, index, assembly = read_experiment_documents(root)
            records = {item.identity: item for item in index.resources}
            def chosen(reference):
                item = records.get(reference.identity)
                if item is None:
                    self.missing()
                return item
            def summary(record):
                return dict(id=record.key, name=record.name, description=record.description,
                            row_version=digest, editable=editable)
            def listed(items, **extra):
                try:
                    page, size = int(query.get("page", 1)), int(query.get("page_size", 20))
                    bounds(page, size)
                except (ValueError, TypeError) as exc:
                    raise ExperimentResourceError(422, "资源分页参数无效") from exc
                needle = query.get("q", "").strip().casefold()
                filtered = []
                for item in items:
                    search = item.pop("_search", item.get("name", "") + " " + item.get("description", "") + " " + item.get("id", ""))
                    if not needle or needle in search.casefold():
                        filtered.append(item)
                items = filtered
                total = len(items)
                return page_result(items[(page - 1) * size:page * size], total, page, size,
                                   editable=editable, row_version=digest, **extra)
            if family in ("maps", "map-editor", "spatial-assets"):
                record = chosen(assembly.map)
                world = dict(record.definition, world_key=record.key, world_name=record.name)
                if family == "maps":
                    if key:
                        if key != experiment_id:
                            self.missing()
                        return self.map_detail(world, digest, editable, experiment_id, validated=validate_execution)
                    geometry = world.get("definition", {})
                    return listed([dict(summary(record), id=experiment_id, map_key=record.key,
                                        dimensions=copy.deepcopy(geometry.get("size")), tile_size=geometry.get("tile_size"), validation=None)])
                if resource == "map-editor/ville-document":
                    return editor_world(world).get("definition", {}).get("editor_v2") or {}
                contracts = world.get("definition", {}).get("editor", {}).get("spatial_assets", {})
                if key:
                    if key not in contracts:
                        self.missing()
                    contract = contracts[key]
                    return dict(id=key, asset_key=key, name=contract.get("name") or key,
                                asset_kind=contract.get("kind", "OBJECT"), contract=copy.deepcopy(contract),
                                row_version=digest, editable=editable)
                return listed([dict(id=asset_key, asset_key=asset_key, name=contract.get("name") or asset_key,
                                    description=contract.get("summary", ""), asset_kind=contract.get("kind", "OBJECT"),
                                    row_version=digest, editable=editable)
                               for asset_key, contract in sorted(contracts.items())
                               if not query.get("kind") or contract.get("kind", "OBJECT") == query["kind"]])
            if family == "skills":
                skills = [record for record in index.resources if record.kind == "skill"]
                def skill_summary(record):
                    folder = str(Path(record.definition["entrypoint"]).parent.as_posix()) + "/"
                    return dict(summary(record), name=record.key, kind=record.definition["skill_kind"],
                                path=record.definition["entrypoint"], storage="experiment",
                                children=[item.key for item in record.dependencies],
                                scripts=[path[len(folder):] for path in record.attachments if path.startswith(folder + "scripts/")])
                if key:
                    record = records.get(("skill", key))
                    if record is None:
                        self.missing()
                    entry = dict(skill_id=key, path=record.definition["entrypoint"], editor_kind=record.definition["skill_kind"])
                    detail = self.skill(root, entry)
                    detail.update(row_version=digest, editable=editable)
                    if len(parts) > 2 and parts[2] == "dependencies":
                        return {"skill": key, "scripts": detail["scripts"],
                                "skills": [skill_summary(chosen(item)) for item in record.dependencies],
                                "mcp": referenced_mcp_tools(detail["markdown"])}
                    if len(parts) > 2 and parts[2] == "history":
                        return {"items": [{"content_hash": detail["content_hash"], "updated_at": detail["updated_at"]}]}
                    return detail
                counts = {kind: sum(item.definition["skill_kind"] == kind for item in skills) for kind in ("atomic", "pack", "brain")}
                return listed([skill_summary(item) for item in sorted(skills, key=lambda item: (item.definition["skill_kind"], item.key))
                               if not query.get("kind") or item.definition["skill_kind"] == query["kind"]
                               or (query["kind"] == "skill" and item.definition["skill_kind"] in {"atomic", "pack"})], counts=counts)
            if family == "agents":
                placements = {item.agent.key: item for item in assembly.placements}
                if key:
                    if key not in placements:
                        self.missing()
                    placement = placements[key]
                    record = chosen(placement.agent)
                    agent = dict(copy.deepcopy(record.definition), agent_key=key, name=record.name,
                                 coord=list(placement.coord), spatial=copy.deepcopy(placement.spatial))
                    return dict(summary(record), agent_key=key, definition=agent)
                items = []
                enabled_filter = query.get("enabled", "all")
                completeness = query.get("completeness", "all")
                if enabled_filter not in {"all", "enabled", "disabled"} or completeness not in {"all", "complete", "incomplete"}:
                    raise ExperimentResourceError(422, "智能体筛选条件无效")
                default_model = chosen(assembly.models["chat"]).definition.get("chat", {}).get("model", "")
                for agent_key, placement in sorted(placements.items()):
                    record = chosen(placement.agent)
                    portrait = record.definition.get("portrait_asset")
                    enabled = record.definition.get("enabled", True)
                    scratch = record.definition.get("scratch", {})
                    complete = bool(record.name and scratch.get("daily_plan") and len(placement.coord) == 2)
                    address = placement.spatial.get("address", {})
                    initial = address.get("initial_location") or address.get("living_area") or []
                    location = " > ".join(initial) if isinstance(initial, list) else str(initial)
                    model = record.definition.get("model_override") or default_model
                    if enabled_filter != "all" and enabled != (enabled_filter == "enabled"):
                        continue
                    if completeness != "all" and complete != (completeness == "complete"):
                        continue
                    if query.get("location", "").casefold() not in location.casefold():
                        continue
                    if query.get("model", "").casefold() not in str(model).casefold():
                        continue
                    search = " ".join(str(value) for value in (record.name, record.description, record.key,
                        scratch.get("innate", ""), scratch.get("learned", ""), location, model,
                        " ".join(record.definition.get("tags", []))))
                    items.append(dict(summary(record), agent_key=agent_key,
                                      enabled=enabled, coord=list(placement.coord),
                                      currently=record.definition.get("currently") or next(iter(record.definition.get("goals", [])), ""),
                                      scratch={name: scratch.get(name) for name in ("age", "innate")},
                                      definition_complete=complete, initial_location=copy.deepcopy(initial),
                                      model_override=record.definition.get("model_override"), model=model, _search=search,
                                      image_url=f"/api/studio/experiments/{experiment_id}/assets/{portrait.removeprefix('assets/')}?width=96" if portrait else None))
                return listed(items)
            if family == "crowds":
                crowds = [chosen(reference) for reference in assembly.crowds]
                if key:
                    record = next((item for item in crowds if item.key == key), None)
                    if record is None:
                        self.missing()
                    members = [item["key"] for item in record.definition.get("members", [])]
                    member_summaries = []
                    for member_key in members:
                        agent = records.get(("agent", member_key))
                        if agent is None:
                            member_summaries.append(dict(id=member_key, name=member_key, missing=True))
                            continue
                        portrait = agent.definition.get("portrait_asset")
                        member_summaries.append(dict(id=member_key, agent_key=member_key, name=agent.name,
                            image_url=f"/api/studio/experiments/{experiment_id}/assets/{portrait.removeprefix('assets/')}?width=96" if portrait else None))
                    return dict(summary(record), crowd_key=key, agent_keys=members, agent_ids=members, members=member_summaries)
                return listed([dict(summary(record), crowd_key=record.key, member_count=len(record.definition.get("members", [])))
                               for record in sorted(crowds, key=lambda item: item.key)])
            raise ExperimentResourceError(404, "实验资源入口不存在")

    @staticmethod
    def missing():
        raise ExperimentResourceError(404, "实验中没有这个资源")

    def prepare_trial(self, experiment_id, key, body, *, destination):
        with self.package(experiment_id, validate_execution=True) as (root, ep, digest, editable):
            registry = self.registry(root)
            entries = {item["skill_id"]: item for item in registry["skills"]}
            if key not in entries:
                self.missing()
            pending, snapshots = [key], {}
            while pending:
                current = pending.pop()
                if current in snapshots:
                    continue
                item = self.skill(root, entries[current])
                snapshots[current] = {**item, "scripts": item["script_sources"]}
                pending.extend(entries[current]["dependencies"])
            config = _experiment_definition(root)[1]["models"]["chat"]
        model = config.get("resolved_model") or config.get("model")
        if not model or model == "auto" or not config.get("base_url"):
            raise PackageError("请先在当前实验的模型页填写明确的模型和连接地址")
        credential = config.get("credential_env")
        api_key = HostModelCredentials(self.workspace.database, self.workspace.package_root.parent).resolve(credential) if credential else ""
        prepare_copied_trial(snapshots, key, body["input_text"], body.get("context", {}),
                             destination=destination, config=config, base_url=config["base_url"], model=model)
        return {"GA_SKILL_TRIAL_KEY": api_key}

    def write(self, experiment_id, resource, method, body):
        expected = body.get("row_version") or body.get("expected_content_sha256")
        with self.package(experiment_id, write=True, expected=expected) as (root, ep, digest, editable):
            parts = resource.strip("/").split("/")
            family, key = parts[0], parts[1] if len(parts) > 1 else None
            changes = {}
            definition = _experiment_definition(root)[1]
            resources = read_experiment_resource_set(root)
            registry = self.registry(root)
            if family == "maps" and key and method in ("PUT", "POST"):
                if key != experiment_id:
                    self.missing()
                if method == "POST" and parts[2:] != ["validate"]:
                    raise ExperimentResourceError(405, "不支持此地图操作")
                world = copy.deepcopy(body.get("world") or definition["world"])
                assets = world.setdefault("assets", [])
                known = {item["logical_path"] for item in assets}
                for source in world.get("definition", {}).get("editor_v2", {}).get("material_sources", []):
                    logical = source.get("bundled_path")
                    if logical and logical.startswith("assets/") and logical not in known:
                        target = root / validate_package_path(logical)
                        target.resolve().relative_to(root.resolve())
                        content = target.read_bytes()
                        assets.append({"logical_path": logical, "asset_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
                                       "media_type": source.get("media_type") or "image/png", "size": len(content)})
                        known.add(logical)
                world, semantic = build_semantic_index(world)
                definition["world"] = world
            elif family == "skills" and method == "POST" and not key:
                if body.get("kind") == "brain":
                    raise PackageError("实验已有一个大脑，请编辑当前大脑")
                key = SkillRegistry.normalize_name(body["name"])
                if any(item["skill_id"] == key for item in registry["skills"]):
                    raise PackageError("实验中已存在同名技能")
                description = str(body.get("description", "")).strip()
                if not description:
                    raise PackageError("请填写技能用途")
                path = f"skills/items/{key}/SKILL.md"
                markdown = f"---\nname: {key}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n# {key}\n\n请填写技能说明。\n"
                resources.resources.append(ResourceRecord(kind="skill", key=key, name=key, description=description,
                    definition={"skill_kind": body.get("kind", "atomic"), "entrypoint": path}, attachments=[path]))
                changes[path] = markdown.encode()
            elif family == "skills" and key and method == "DELETE":
                entry = next((item for item in registry["skills"] if item["skill_id"] == key), None)
                if entry is None:
                    self.missing()
                resources.resources = [item for item in resources.resources if item.identity != ("skill", key)]
                for path in (root / entry["path"]).parent.rglob("*"):
                    if path.is_file():
                        changes[path.relative_to(root).as_posix()] = None
            elif family == "skills" and key and method == "PUT":
                entry = next((item for item in registry["skills"] if item["skill_id"] == key), None)
                if entry is None:
                    self.missing()
                detail = self.skill(root, entry, markdown=body["markdown"], scripts=body.get("scripts", {}))
                entry["dependencies"] = detail["children"]
                if re.search(r"\$" + re.escape(key) + r"(?![a-z0-9-])", body["markdown"]):
                    raise PackageError("技能不能把自身声明为子技能依赖")
                entry["content_sha256"] = hashlib.sha256(body["markdown"].encode()).hexdigest()
                changes[entry["path"]] = body["markdown"].encode()
                folder = Path(entry["path"]).parent
                for old in (root / folder / "scripts").rglob("*"):
                    if old.is_file():
                        changes[old.relative_to(root).as_posix()] = None
                changes.update({(folder / relative).as_posix(): content.encode() for relative, content in detail["script_sources"].items()})
                record = resources.get(ResourceRef(kind="skill", key=key))
                record.description = detail["description"]
                record.dependencies = [ResourceRef(kind="skill", key=child) for child in detail["children"]]
            elif family == "crowds" and method in ("PUT", "POST", "DELETE"):
                document = definition
                crowds = document.setdefault("crowds", [])
                if method == "POST":
                    key = f"crowd-{uuid4().hex[:12]}"
                    crowd = {"crowd_key": key}
                    crowds.append(crowd)
                else:
                    crowd = next((item for item in crowds if item["crowd_key"] == key), None)
                    if crowd is None:
                        self.missing()
                if method == "DELETE":
                    crowds.remove(crowd)
                else:
                    members = list(dict.fromkeys(body.get("agent_ids", [])))
                    if set(members) - {agent["agent_key"] for agent in document["agents"]}:
                        raise PackageError("人群成员必须是当前实验的智能体")
                    name = str(body.get("name", "")).strip()
                    if not name:
                        raise PackageError("请填写人群名称")
                    crowd.update(name=name, description=str(body.get("description", "")), agent_keys=members)
            elif family == "spatial-assets" and method in ("PUT", "POST", "DELETE"):
                world = definition["world"]
                contracts = world["definition"].setdefault("editor", {}).setdefault("spatial_assets", {})
                if method == "POST":
                    key = body.get("asset_key") or f"asset-{uuid4().hex[:12]}"
                    if key in contracts:
                        raise PackageError("当前实验已存在此素材 key")
                elif key not in contracts:
                    self.missing()
                if method == "DELETE":
                    del contracts[key]
                else:
                    contract = body.get("contract") or {"name": body.get("name", key), "kind": body.get("asset_kind", "OBJECT"),
                                                        "appearance": {"mode": "COLOR", "color": "#dce9df"}}
                    contracts[key] = SpatialAssetContract.model_validate(contract).model_dump(mode="json")
                world, semantic = build_semantic_index(world)
                definition["world"] = world
            else:
                raise ExperimentResourceError(405, "此实验资源不支持该操作")
            self.commit(root, changes, definition, resources)
        return None if method == "DELETE" else self.read(experiment_id, f"{family}/{key}", {})
