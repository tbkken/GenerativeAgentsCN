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
            with package_lock(location), open_package(location) as root:
                validate = validate_experiment_directory if write or validate_execution else validate_experiment_integrity
                manifest = validate(root)
                digest = read_json(root / "integrity/sha256.json")["root_sha256"]
                if write and expected != digest:
                    raise WorkspaceConflictError("实验内容已变化，请重新打开当前编辑页后保存；本次未覆盖已有内容。")
                yield root, manifest.entrypoints.model_dump(exclude_none=True), digest, location.is_dir()

    def commit(self, root, changes):
        """Rollback every touched file, including registry/index/integrity, on failure."""
        changes = dict(changes)
        paths = {root / validate_package_path(key) for key in changes}
        paths.add(root / "integrity/sha256.json")
        for path in paths:
            path.resolve().relative_to(root.resolve())
        before = {path: path.read_bytes() if path.is_file() else None for path in paths}
        try:
            for relative, content in changes.items():
                target = root / relative
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_bytes(target, content)
            write_integrity_manifest(root)
            manifest = validate_experiment_directory(root)
        except Exception:
            for path, content in before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, content)
            raise
        digest = read_json(root / "integrity/sha256.json")["root_sha256"]
        self.workspace.catalog.record_validated_experiment(root, manifest, digest)
        return digest

    @staticmethod
    def skill(root, entry, *, markdown=None, scripts=None):
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
        with self.package(experiment_id, validate_execution=validate_execution) as (root, ep, digest, editable):
            parts = resource.strip("/").split("/")
            family, key = parts[0], parts[1] if len(parts) > 1 else None
            world = read_json(root / ep["world"]) if family in ("maps", "map-editor", "spatial-assets") else None
            if family == "maps":
                if key and key != experiment_id:
                    self.missing()
                detail = self.map_detail(world, digest, editable, experiment_id, validated=validate_execution)
                return detail if key else {"items": [detail], "total": 1}
            if resource == "map-editor/ville-document":
                return editor_world(world).get("definition", {}).get("editor_v2") or {}
            if family == "skills":
                registry = read_json(root / ep["skills"])
                entries = registry["skills"]
                if key:
                    entry = next((item for item in entries if item["skill_id"] == key), None)
                    if entry is None:
                        raise ExperimentResourceError(404, "实验中没有这个技能")
                    detail = self.skill(root, entry)
                    detail.update(row_version=digest, editable=editable)
                    if len(parts) > 2 and parts[2] == "dependencies":
                        return {"skill": key, "scripts": detail["scripts"],
                                "skills": [self.skill(root, item) for item in entries if item["skill_id"] in entry["dependencies"]],
                                "mcp": referenced_mcp_tools(detail["markdown"])}
                    if len(parts) > 2 and parts[2] == "history":
                        return {"items": [{"content_hash": detail["content_hash"], "updated_at": detail["updated_at"]}]}
                    return detail
                items = [self.skill(root, entry) for entry in entries]
                counts = {kind: sum(item["kind"] == kind for item in items) for kind in ("atomic", "pack", "brain")}
                items = [item for item in items if (not query.get("kind") or item["kind"] == query["kind"])
                         and query.get("q", "").casefold() in (item["name"] + item["description"]).casefold()]
                return {"items": items, "counts": counts, "editable": editable}
            if family in ("agents", "crowds"):
                document = read_json(root / ep["agents"])
                if family == "agents":
                    items = [dict(id=agent["agent_key"], agent_key=agent["agent_key"], name=agent["name"],
                                  definition=agent, row_version=digest, editable=editable) for agent in document["agents"]]
                else:
                    items = [dict(item, id=item["crowd_key"], agent_ids=item["agent_keys"], row_version=digest,
                                  editable=editable) for item in document.get("crowds", [])]
                if key:
                    return next((item for item in items if item["id"] == key), None) or self.missing()
                return {"items": items, "editable": editable, "row_version": digest}
            if family == "spatial-assets":
                contracts = world.get("definition", {}).get("editor", {}).get("spatial_assets", {})
                items = [dict(id=asset_key, asset_key=asset_key, name=contract.get("name") or asset_key,
                              asset_kind=contract.get("kind", "OBJECT"), contract=contract,
                              row_version=digest, editable=editable) for asset_key, contract in contracts.items()]
                if key:
                    return next((item for item in items if item["id"] == key), None) or self.missing()
                return {"items": [item for item in items if not query.get("kind") or item["asset_kind"] == query["kind"]]}
            raise ExperimentResourceError(404, "实验资源入口不存在")

    @staticmethod
    def missing():
        raise ExperimentResourceError(404, "实验中没有这个资源")

    def prepare_trial(self, experiment_id, key, body, *, destination):
        with self.package(experiment_id, validate_execution=True) as (root, ep, digest, editable):
            registry = read_json(root / ep["skills"])
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
            config = read_json(root / ep["models"])["chat"]
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
            if family == "maps" and key and method in ("PUT", "POST"):
                if key != experiment_id:
                    self.missing()
                if method == "POST" and parts[2:] != ["validate"]:
                    raise ExperimentResourceError(405, "不支持此地图操作")
                world = copy.deepcopy(body.get("world") or read_json(root / ep["world"]))
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
                registry = read_json(root / ep["skills"])
                roots = self.workspace._object_skill_names(world)
                registry["object_roots"] = sorted(roots)
                for entry in registry["skills"]:
                    if entry["kind"] != "brain":
                        entry["kind"] = "object" if entry["skill_id"] in roots else "sub_skill"
                changes.update({ep["world"]: encoded(world), ep["semantic_index"]: encoded(semantic), ep["skills"]: encoded(registry)})
            elif family == "skills" and method == "POST" and not key:
                registry = read_json(root / ep["skills"])
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
                registry["skills"].append(dict(skill_id=key, kind="sub_skill", editor_kind=body.get("kind", "atomic"), path=path, dependencies=[],
                                                content_sha256=hashlib.sha256(markdown.encode()).hexdigest()))
                changes.update({path: markdown.encode(), ep["skills"]: encoded(registry)})
            elif family == "skills" and key and method == "DELETE":
                registry = read_json(root / ep["skills"])
                entry = next((item for item in registry["skills"] if item["skill_id"] == key), None)
                if entry is None:
                    self.missing()
                registry["skills"].remove(entry)
                for path in (root / entry["path"]).parent.rglob("*"):
                    if path.is_file():
                        changes[path.relative_to(root).as_posix()] = None
                changes[ep["skills"]] = encoded(registry)
            elif family == "skills" and key and method == "PUT":
                registry = read_json(root / ep["skills"])
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
                changes[ep["skills"]] = encoded(registry)
            elif family == "crowds" and method in ("PUT", "POST", "DELETE"):
                document = read_json(root / ep["agents"])
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
                changes[ep["agents"]] = encoded(document)
            elif family == "spatial-assets" and method in ("PUT", "POST", "DELETE"):
                world = read_json(root / ep["world"])
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
                changes.update({ep["world"]: encoded(world), ep["semantic_index"]: encoded(semantic)})
            else:
                raise ExperimentResourceError(405, "此实验资源不支持该操作")
            self.commit(root, changes)
        return None if method == "DELETE" else self.read(experiment_id, f"{family}/{key}", {})
