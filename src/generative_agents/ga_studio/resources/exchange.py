"""Transactional exchange of public author resources through Protocol packages.

The package owns portable keys and bytes.  This module alone translates those
keys into Studio row identities; Runtime never consults the exchange state.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import mimetypes
import tempfile
from collections import OrderedDict
from threading import RLock
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import func, select, true
from sqlalchemy.orm import defer
from sqlalchemy.exc import IntegrityError
from pydantic import TypeAdapter

from generative_agents.ga_protocol.packages.io import PackageError, canonical_json_bytes, seal_directory, open_package, read_json
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceRef, ResourceSet, read_resource_set,
    resource_content_hash, write_config_package, skill_document, validate_resource_set,
)
from generative_agents.ga_protocol.schemas.experiment import WorldConfig, ChatModelConfig, EmbeddingModelConfig
from generative_agents.ga_protocol.schemas.resources import AgentCore
from generative_agents.ga_protocol.schemas.spatial_assets import SpatialAssetContract
from generative_agents.ga_studio.resources.assets import AssetService
from generative_agents.ga_studio.resources.catalog import StudioAgentDefinition
from generative_agents.ga_studio.resources.errors import ServiceError
from generative_agents.ga_studio.resources.maps import normalize_public_world, world_hash
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.models import (
    Asset, SpatialAssetDefinition, StudioAgent, StudioCrowd, StudioEvaluator,
    StudioModelPreset, StudioSkill, StudioResourceExchangeState, WorldMap,
)


_MODELS = {
    "map": (WorldMap, "map_key"), "agent": (StudioAgent, "agent_key"),
    "crowd": (StudioCrowd, "crowd_key"), "skill": (StudioSkill, "skill_key"),
    "model": (StudioModelPreset, "preset_key"),
    "spatial_asset": (SpatialAssetDefinition, "asset_key"),
    "evaluator": (StudioEvaluator, "evaluator_key"),
}


class ResourceExchangeError(ServiceError):
    def __init__(self, message, *, code="RESOURCE_EXCHANGE_INVALID", status_code=422, details=None):
        super().__init__(code, message, status_code=status_code, details=details)


def _ref(value):
    return value if isinstance(value, ResourceRef) else ResourceRef(**value)


def _identity(value):
    return value.kind, value.key


def _ref_json(value):
    result = {"kind": value.kind, "key": value.key}
    if getattr(value, "expected_sha256", None):
        result["expected_sha256"] = value.expected_sha256
    return result


def resource_extra_files(session, kind, resource_id):
    """Return copied Skill attachments beyond editable script text."""
    state = session.get(StudioResourceExchangeState, (kind, resource_id), options=[defer(StudioResourceExchangeState.extra_files_json)])
    return {path: base64.b64decode(content, validate=True)
            for path, content in (state.extra_files_json if state else {}).items()}


def pending_resource_dependencies(session, kind, resource_id):
    dependencies = session.scalar(select(StudioResourceExchangeState.dependencies_json).where(
        StudioResourceExchangeState.resource_kind == kind, StudioResourceExchangeState.resource_id == resource_id))
    return [{key: value for key, value in item.items() if key != "resource_id"}
            for item in dependencies or [] if not item.get("resource_id")]


def _json_keys(session, model, column, *predicates):
    keys = func.json_each(column).table_valued("key")
    return list(session.scalars(select(keys.c.key).select_from(model).join(keys, true()).where(*predicates)))


def delete_resource_exchange_state(session, kind, resource_id):
    state = session.get(StudioResourceExchangeState, (kind, resource_id), options=[defer(StudioResourceExchangeState.extra_files_json)])
    if state is not None:
        session.delete(state)


def require_resolved_resource_dependencies(session, kind, resource_id, *, _seen=None):
    """Block experiment materialization of explicitly unbound author resources."""
    seen = set() if _seen is None else _seen
    identity = (kind, resource_id)
    if identity in seen:
        return
    seen.add(identity)
    state = session.get(StudioResourceExchangeState, identity)
    for dependency in state.dependencies_json if state else []:
        target_id = dependency.get("resource_id")
        model = _MODELS[dependency["kind"]][0]
        target = session.get(model, target_id) if target_id else None
        if target is None or getattr(target, "archived_at", None) is not None or getattr(target, _MODELS[dependency["kind"]][1]) != dependency["key"]:
            raise ResourceExchangeError(
                f"资源依赖尚未绑定：{kind}/{resource_id} → {dependency['kind']}/{dependency['key']}；请先在基础配置中明确选择依赖",
                code="RESOURCE_DEPENDENCY_UNBOUND",
            )
        require_resolved_resource_dependencies(session, dependency["kind"], target_id, _seen=seen)


def bind_saved_resource_dependencies(session, kind, resource_id, references):
    """An explicit author save confirms the current local dependency choices."""
    state = session.get(StudioResourceExchangeState, (kind, resource_id), options=[defer(StudioResourceExchangeState.extra_files_json)])
    if state is None:
        return
    dependencies = []
    for raw in references:
        reference = _ref(raw)
        model, key_field = _MODELS[reference.kind]
        row = session.scalar(select(model).where(getattr(model, key_field) == reference.key))
        dependencies.append({**_ref_json(reference), "resource_id": row.id if row is not None and getattr(row, "archived_at", None) is None else None})
    state.dependencies_json = dependencies


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _media_type(data, path):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _asset_path(data, path):
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
        _media_type(data, path), PurePosixPath(str(path)).suffix.lower() or ".bin"
    )
    return f"assets/{hashlib.sha256(data).hexdigest()}{extension}"


def _replace_paths(value, mapping):
    if isinstance(value, dict):
        return {key: _replace_paths(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_paths(child, mapping) for child in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def _skill_dependencies(value):
    found = set()
    def visit(node):
        if isinstance(node, dict):
            for binding in node.get("skill_bindings") or []:
                if isinstance(binding, dict) and binding.get("skill_name"):
                    found.add(str(binding["skill_name"]))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return [ResourceRef(kind="skill", key=key) for key in sorted(found)]


def _clean_models(value, purpose=None):
    """Keep transport configuration and environment names, never credentials."""
    forbidden = {"api_key", "apikey", "api-key", "token", "access_token", "password",
                 "secret", "secret_ref", "credentials", "authorization",
                 "headers", "extra_headers", "default_headers"}
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key.casefold() in forbidden:
                continue
            if key == "base_url" and isinstance(child, str):
                url = urlsplit(child)
                host = url.hostname or ""
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                if url.port is not None:
                    host += f":{url.port}"
                child = urlunsplit((url.scheme, host, url.path, "", ""))
            result[key] = _clean_models(child, key if key in {"chat", "embedding"} else purpose)
        if purpose in {"chat", "embedding"} and value.get("secret_ref") and not result.get("credential_env"):
            result["credential_env"] = "GA_EMBEDDING_API_KEY" if purpose == "embedding" else "GA_CHAT_API_KEY"
        return result
    if isinstance(value, list):
        return [_clean_models(item, purpose) for item in value]
    return value


def _model_definition(value):
    cleaned = _clean_models(copy.deepcopy(value))
    return {purpose: TypeAdapter(ChatModelConfig if purpose == "chat" else EmbeddingModelConfig)
            .validate_python(config).model_dump(mode="json") for purpose, config in cleaned.items()}


class ResourceExchangeService:
    def __init__(self, database, *, asset_store):
        self.database = database
        self.asset_store = asset_store
        self._imports = OrderedDict()
        self._imports_lock = RLock()

    @staticmethod
    def _kind(kind):
        kind = "skill" if kind == "brain" else kind
        if kind not in _MODELS:
            raise ResourceExchangeError(f"不支持的资源类型：{kind}")
        return kind

    def catalog(self, kind, *, query="", page=1, page_size=20):
        from .listing import page_result, query_page, search_predicate
        current = self._kind(kind)
        model, key_field = _MODELS[current]
        key = getattr(model, key_field)
        name = model.skill_key if current == "skill" else model.name
        columns = [model.id, key.label("key"), name.label("name"), model.description]
        if current == "skill":
            columns.append(model.kind.label("skill_kind"))
        filters = [model.archived_at.is_(None)] if hasattr(model, "archived_at") else []
        if current == "skill":
            filters.append(model.kind == "brain" if kind == "brain" else model.kind != "brain")
        if query.strip():
            filters.append(search_predicate(query, key, name, model.description))
        with self.database.session_factory() as session:
            rows, total = query_page(session, model, columns, predicates=filters, order=(key,), page=page, page_size=page_size)
            dependencies = dict(session.execute(select(StudioResourceExchangeState.resource_id,
                StudioResourceExchangeState.dependencies_json).where(
                    StudioResourceExchangeState.resource_kind == current,
                    StudioResourceExchangeState.resource_id.in_([row["id"] for row in rows]))).all())
        for row in rows:
            row["kind"] = current
            row.setdefault("skill_kind", None)
            row["pending_dependencies"] = [{key: value for key, value in item.items() if key != "resource_id"}
                                            for item in dependencies.get(row["id"], []) if not item.get("resource_id")]
        return page_result(rows, total, page, page_size)

    def _find(self, session, kind, key_or_id, *, metadata_only=False):
        model, key_field = _MODELS[self._kind(kind)]
        options = [defer(model.markdown), defer(model.scripts_json)] if metadata_only and model is StudioSkill else []
        row = session.get(model, key_or_id, options=options)
        if row is None:
            row = session.scalar(select(model).options(*options).where(getattr(model, key_field) == key_or_id))
        return row

    def _asset_bytes(self, asset):
        if asset is None:
            raise ResourceExchangeError("资源引用的附件不存在，无法导出完整配置")
        try:
            data = bytes(asset.content_blob) if asset.content_blob is not None else self.asset_store.resolve(
                asset.relative_path, expected_sha256=asset.sha256
            ).read_bytes()
        except (OSError, ValueError) as exc:
            raise ResourceExchangeError(f"附件不存在或校验失败：{asset.logical_name}") from exc
        if hashlib.sha256(data).hexdigest() != asset.sha256 or len(data) != asset.size_bytes:
            raise ResourceExchangeError(f"附件校验失败：{asset.logical_name}")
        return data

    def _image(self, session, files, *, asset_id=None, digest=None, path=None, metadata_only=False):
        asset = session.get(Asset, asset_id, options=[defer(Asset.content_blob)]) if asset_id else None
        if asset_id and asset is None:
            raise ResourceExchangeError(f"资源引用的附件不存在：{asset_id}")
        if asset is None and digest:
            asset = session.scalar(select(Asset).options(defer(Asset.content_blob)).where(Asset.sha256 == str(digest).removeprefix("sha256:")))
        if asset is None and path:
            # A portable asset filename is content-addressed on re-import.
            stem = PurePosixPath(path).stem
            if len(stem) == 64:
                asset = session.scalar(select(Asset).options(defer(Asset.content_blob)).where(Asset.sha256 == stem))
            if asset is None:
                asset = session.scalar(select(Asset).options(defer(Asset.content_blob)).where(Asset.relative_path == path))
        if asset is not None and digest and asset.sha256 != str(digest).removeprefix("sha256:"):
            raise ResourceExchangeError(f"附件引用与内容摘要不一致：{asset.logical_name}")
        if metadata_only:
            if asset is None:
                raise ResourceExchangeError("资源引用的附件不存在，无法导出完整配置")
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
                asset.media_type, PurePosixPath(asset.logical_name).suffix.lower() or ".bin")
            return f"assets/{asset.sha256}{extension}", asset
        data = self._asset_bytes(asset)
        target = _asset_path(data, asset.logical_name)
        files[target] = data
        return target, asset

    def _record(self, session, kind, row, files, *, metadata_only=False):
        kind = self._kind(kind)
        _, key_field = _MODELS[kind]
        key = getattr(row, key_field)
        name = key if kind == "skill" else row.name
        attachments = set()
        dependencies = []
        if kind == "agent":
            definition = copy.deepcopy(row.definition_json)
            definition.pop("agent_key", None)
            definition.pop("name", None)
            for role in ("portrait", "sprite"):
                asset_id = definition.pop(f"{role}_asset_id", None)
                definition[f"{role}_asset"] = None
                if asset_id:
                    path, _ = self._image(session, files, asset_id=asset_id, metadata_only=metadata_only)
                    definition[f"{role}_asset"] = path
                    attachments.add(path)
        elif kind == "crowd":
            members = []
            for agent_id in row.agent_ids_json or []:
                agent = session.get(StudioAgent, agent_id)
                if agent is None:
                    raise ResourceExchangeError(f"人群成员已不存在：{agent_id}")
                members.append(ResourceRef(kind="agent", key=agent.agent_key))
            state = session.get(StudioResourceExchangeState, (kind, row.id), options=[defer(StudioResourceExchangeState.extra_files_json)])
            if state:
                # Retain unselected members as portable keys, never fake local IDs.
                members = [ResourceRef(kind=item["kind"], key=item["key"], expected_sha256=item.get("expected_sha256"))
                           for item in state.dependencies_json]
            dependencies = members
            definition = {"members": [_ref_json(item) for item in members]}
        elif kind == "skill":
            entrypoint = f"skills/items/{key}/SKILL.md"
            if not metadata_only:
                files[entrypoint] = row.markdown.encode("utf-8")
            attachments.add(entrypoint)
            scripts = ({path: None for path in _json_keys(session, StudioSkill, StudioSkill.scripts_json, StudioSkill.id == row.id)}
                       if metadata_only else row.scripts_json or {})
            for relative, text in scripts.items():
                path = f"skills/items/{key}/{relative}"
                if not metadata_only:
                    files[path] = text.encode("utf-8")
                attachments.add(path)
            extra_files = ({path: None for path in _json_keys(session, StudioResourceExchangeState,
                StudioResourceExchangeState.extra_files_json, StudioResourceExchangeState.resource_kind == kind,
                StudioResourceExchangeState.resource_id == row.id)} if metadata_only else resource_extra_files(session, kind, row.id))
            for relative, content in extra_files.items():
                path = f"skills/items/{key}/{relative}"
                if not metadata_only:
                    files[path] = content
                attachments.add(path)
            definition = {"skill_kind": row.kind, "entrypoint": entrypoint}
            dependencies = [ResourceRef(kind="skill", key=item) for item in row.children_json or []]
        elif kind in {"model", "evaluator"}:
            definition = _model_definition(row.config_json) if kind == "model" else copy.deepcopy(row.config_json)
        elif kind == "spatial_asset":
            definition = SpatialAssetContract.model_validate(row.contract_json).model_dump(mode="json")
            self._spatial_images(session, definition, files, attachments, metadata_only=metadata_only)
            dependencies = _skill_dependencies(definition)
        else:
            definition = normalize_public_world(row.world_json).model_dump(mode="json", exclude_none=False)
            for field in ("map_id", "map_snapshot_hash", "world_key", "world_name"):
                definition.pop(field, None)
            world = definition.setdefault("definition", {})
            editor = world.get("editor_v2") or {}
            editor.pop("ui_state", None)
            path_map = {}
            references = []
            for reference in definition.get("assets") or []:
                path, asset = self._image(session, files, digest=reference.get("asset_hash"), path=reference.get("logical_path"), metadata_only=metadata_only)
                path_map[reference["logical_path"]] = path
                attachments.add(path)
                references.append({"logical_path": path, "asset_hash": f"sha256:{asset.sha256}",
                                   "media_type": asset.media_type, "size": asset.size_bytes})
            for source in editor.get("material_sources") or []:
                if source.get("kind") not in {"UPLOADED", "BUNDLED"}:
                    continue
                path, asset = self._image(session, files, asset_id=source.get("asset_id"),
                                          digest=source.get("asset_hash"), path=source.get("bundled_path"), metadata_only=metadata_only)
                if source.get("bundled_path"):
                    path_map[source["bundled_path"]] = path
                source.pop("asset_id", None)
                source.update(kind="BUNDLED", bundled_path=path, asset_hash=asset.sha256)
                attachments.add(path)
                references.append({"logical_path": path, "asset_hash": f"sha256:{asset.sha256}",
                                   "media_type": asset.media_type, "size": asset.size_bytes})
            definition = _replace_paths(definition, path_map)
            definition["assets"] = list({item["logical_path"]: item for item in references}.values())
            world = definition["definition"]
            scene = world.get("spatial_scene") or {}
            asset_ids = set((scene.get("palette_refs") or {}).values())
            asset_ids.update(item.get("spatial_asset_id") for item in scene.get("placements") or [])
            contracts = {}
            mapping = {}
            for asset_id in sorted(asset_ids - {None}):
                asset = session.get(SpatialAssetDefinition, asset_id)
                if asset is None:
                    raise ResourceExchangeError(f"地图的空间素材不存在：{asset_id}")
                mapping[asset_id] = asset.asset_key
                contract = SpatialAssetContract.model_validate(asset.contract_json).model_dump(mode="json")
                self._spatial_images(session, contract, files, attachments, metadata_only=metadata_only)
                contracts[asset.asset_key] = contract
            if scene:
                world["spatial_scene"] = _replace_paths(scene, mapping)
                world.setdefault("editor", {})["spatial_assets"] = contracts
            dependencies = _skill_dependencies(definition)
        record = ResourceRecord(kind=kind, key=key, name=name, description=row.description,
                                definition=definition, dependencies=dependencies,
                                attachments=sorted(attachments))
        state = session.get(StudioResourceExchangeState, (kind, row.id), options=[defer(StudioResourceExchangeState.extra_files_json)])
        if state:
            known = {(item["kind"], item["key"]): item for item in state.dependencies_json}
            record.dependencies = [item.model_copy(update={"expected_sha256": known[_identity(item)].get("expected_sha256")})
                                   if _identity(item) in known else item for item in record.dependencies]
        return record

    def _spatial_images(self, session, contract, files, attachments, *, metadata_only=False):
        appearance = contract.get("appearance") or {}
        for variant in [appearance, *(appearance.get("state_variants") or {}).values()]:
            if variant.get("asset_path"):
                path, _ = self._image(session, files, path=variant["asset_path"], metadata_only=metadata_only)
                variant["asset_path"] = path
                attachments.add(path)

    def resource_set(self, kind, resource_id, *, include_dependencies=False):
        requested_kind = kind
        kind = self._kind(kind)
        files, records, visited = {}, [], set()
        with self.database.session_factory() as session:
            root = self._find(session, kind, resource_id)
            if root is None or getattr(root, "archived_at", None) is not None:
                raise ResourceExchangeError("所选公共资源不存在或已归档", status_code=404)
            if requested_kind == "brain" and root.kind != "brain":
                raise ResourceExchangeError("所选资源不是大脑")
            pending = [(kind, root)]
            roots = []
            while pending:
                current_kind, row = pending.pop(0)
                identity = (current_kind, getattr(row, _MODELS[current_kind][1]))
                if identity in visited:
                    continue
                visited.add(identity)
                record = self._record(session, current_kind, row, files)
                records.append(record)
                if not roots:
                    roots.append(ResourceRef(kind=record.kind, key=record.key))
                state = session.get(StudioResourceExchangeState, (current_kind, row.id), options=[defer(StudioResourceExchangeState.extra_files_json)])
                unbound = {(item["kind"], item["key"]) for item in state.dependencies_json if not item.get("resource_id")} if state else set()
                for dependency in record.dependencies:
                    target = self._find(session, dependency.kind, dependency.key)
                    if _identity(dependency) not in unbound and target is not None and getattr(target, "archived_at", None) is None:
                        pending.append((dependency.kind, target))
        complete = ResourceSet(resources=records, files=files, roots=roots)
        for record in records:
            record.dependencies = [item.model_copy(update={"expected_sha256": resource_content_hash(complete.get(item), complete)})
                                   if _identity(item) in complete.by_ref() else item for item in record.dependencies]
        if include_dependencies:
            return complete
        record = records[0]
        return ResourceSet(resources=[record], files={path: files[path] for path in record.attachments}, roots=roots)

    def _export_resources(self, kind, resource_id, include_dependencies):
        # These author exports are complete, portable compositions. This policy
        # belongs here so every caller gets the same behavior as the UI.
        complete = kind in {"crowd", "brain", "skill"} or include_dependencies
        resources = self.resource_set(kind, resource_id, include_dependencies=complete)
        if complete:
            present = resources.by_ref()
            missing = [f"{record.kind}/{record.key} → {dep.kind}/{dep.key}"
                       for record in resources.resources for dep in record.dependencies
                       if dep.identity not in present]
            if missing:
                raise ResourceExchangeError("无法完整导出，关联资源缺失、已归档或尚未绑定：" + "；".join(missing))
        try:
            validate_resource_set(resources, require_dependencies=bool(complete))
        except PackageError as exc:
            raise ResourceExchangeError(f"资源包校验失败：{exc}") from exc
        return resources

    def preview_export(self, kind, resource_id, *, include_dependencies=False):
        # This is a dependency/attachment plan. Actual export validates all bytes.
        complete = kind in {"crowd", "brain", "skill"} or include_dependencies
        current = self._kind(kind)
        with self.database.session_factory() as session:
            root = self._find(session, current, resource_id, metadata_only=True)
            if root is None or getattr(root, "archived_at", None) is not None:
                raise ResourceExchangeError("所选公共资源不存在或已归档", status_code=404)
            if kind == "brain" and root.kind != "brain":
                raise ResourceExchangeError("所选资源不是大脑")
            pending, seen, records = [(current, root)], set(), []
            while pending:
                current_kind, row = pending.pop(0)
                identity = current_kind, row.id
                if identity in seen:
                    continue
                seen.add(identity)
                record = self._record(session, current_kind, row, {}, metadata_only=True)
                records.append(record)
                if not complete:
                    continue
                state = session.get(StudioResourceExchangeState, (current_kind, row.id), options=[defer(StudioResourceExchangeState.extra_files_json)])
                unbound = {(item["kind"], item["key"]) for item in state.dependencies_json if not item.get("resource_id")} if state else set()
                for dependency in record.dependencies:
                    target = self._find(session, dependency.kind, dependency.key, metadata_only=True)
                    if dependency.identity in unbound or target is None or getattr(target, "archived_at", None) is not None:
                        raise ResourceExchangeError(f"无法完整导出，关联资源缺失、已归档或尚未绑定：{record.kind}/{record.key} → {dependency.kind}/{dependency.key}")
                    pending.append((dependency.kind, target))
        graph = {record.identity: [item.identity for item in record.dependencies] for record in records}
        visiting, visited = set(), set()
        def visit(identity):
            if identity in visiting:
                raise ResourceExchangeError(f"资源依赖存在循环：{identity[0]}/{identity[1]}")
            if identity in visited or identity not in graph:
                return
            visiting.add(identity)
            for dependency in graph[identity]:
                visit(dependency)
            visiting.remove(identity)
            visited.add(identity)
        for identity in graph:
            visit(identity)
        return {"roots": [{"kind": records[0].kind, "key": records[0].key}],
                "resources": [{**_ref_json(item), "name": item.name,
                               "skill_kind": item.definition.get("skill_kind"),
                               "attachment_count": len(item.attachments),
                               "dependencies": [_ref_json(dep) for dep in item.dependencies]}
                              for item in records]}

    def export_resource(self, kind, resource_id, destination, *, include_dependencies=False):
        resources = self._export_resources(kind, resource_id, include_dependencies)
        destination = Path(destination)
        with tempfile.TemporaryDirectory(prefix="ga-resource-export-") as temporary:
            root = write_config_package(Path(temporary) / "config", resources, name=resources.resources[0].name)
            return seal_directory(root, destination)

    def materialize_resources(self, selections, *, include_dependencies=True):
        """Copy selected public (kind, ID) resources without an archive round trip."""
        records, files, roots = {}, {}, []
        for kind, resource_id in selections:
            selected = self.resource_set(kind, resource_id, include_dependencies=include_dependencies)
            for record in selected.resources:
                records[record.identity] = record
            files.update(selected.files)
            roots.extend(selected.roots)
        return ResourceSet(resources=list(records.values()), files=files,
                           roots=list({item.identity: item for item in roots}.values()))

    def _load(self, package):
        from generative_agents.ga_protocol.packages.reading import file_fingerprint
        path = Path(package)
        signature = file_fingerprint(path) if path.is_file() else None
        if signature is not None:
            with self._imports_lock:
                cached = self._imports.get(path)
                if cached and cached[0] == signature:
                    self._imports.move_to_end(path)
                    return cached[1]
        try:
            resources = read_resource_set(package)
            result = self._normalize(resources)
        except (PackageError, ValueError) as exc:
            raise ResourceExchangeError(str(exc)) from exc
        if signature is not None:
            if file_fingerprint(path) != signature:
                raise ResourceExchangeError("资源包在校验期间变化，请重新上传")
            size = sum(len(content) for content in result.files.values())
            size += sum(len(canonical_json_bytes(record.model_dump(mode="json"))) for record in result.resources)
            if size <= 32 * 1024 * 1024:
                with self._imports_lock:
                    self._imports[path] = (signature, result, size)
                    self._imports.move_to_end(path)
                    while len(self._imports) > 4 or sum(entry[2] for entry in self._imports.values()) > 64 * 1024 * 1024:
                        self._imports.popitem(last=False)
        return result

    @staticmethod
    def _normalize(resources):
        """Canonical asset locations make identical imports independent of sender paths."""
        files, records = {}, []
        for record in resources.resources:
            mapping = {}
            if record.kind == "skill":
                prefix = PurePosixPath(record.definition["entrypoint"]).parent
                for path in record.attachments:
                    relative = PurePosixPath(path).relative_to(prefix)
                    mapping[path] = f"skills/items/{record.key}/{relative.as_posix()}"
            else:
                for path in record.attachments:
                    mapping[path] = _asset_path(resources.files[path], path)
            for old, new in mapping.items():
                files[new] = resources.files[old]
            definition = _replace_paths(record.definition, mapping)
            if record.kind == "model":
                definition = _model_definition(definition)
            elif record.kind == "agent":
                definition = AgentCore.model_validate(definition).model_dump(mode="json")
            elif record.kind == "spatial_asset":
                definition = SpatialAssetContract.model_validate(definition).model_dump(mode="json")
            records.append(ResourceRecord(kind=record.kind, key=record.key, name=record.name,
                                          description=record.description, definition=definition,
                                          dependencies=record.dependencies,
                                          attachments=sorted(set(mapping.values()))))
        normalized = ResourceSet(resources=records, files=files, roots=resources.roots)
        for record in records:
            record.dependencies = [item.model_copy(update={"expected_sha256": resource_content_hash(normalized.get(item), normalized)})
                                   if _identity(item) in normalized.by_ref() else item for item in record.dependencies]
        return normalized

    @staticmethod
    def _selection(resources, selections, include_dependencies):
        by_ref = {_identity(item): item for item in resources.resources}
        selected = {_identity(_ref(item)) for item in (selections if selections is not None else resources.roots or resources.resources)}
        missing = selected - set(by_ref)
        if missing:
            raise ResourceExchangeError(f"所选资源不在导入包中：{sorted(missing)}")
        pending = list(selected)
        while pending:
            record = by_ref[pending.pop()]
            if not include_dependencies and not (include_dependencies is None and record.kind in {"crowd", "skill"}):
                continue
            for dependency in record.dependencies:
                identity = _identity(dependency)
                if identity in by_ref and identity not in selected:
                    selected.add(identity)
                    pending.append(identity)
        return selected

    def preview_import(self, package, *, selections=None, include_dependencies=None):
        resources = self._load(package)
        selected = self._selection(resources, selections, include_dependencies)
        with self.database.session_factory() as session:
            result = self._preview(session, resources, selected)
        result["roots"] = [_ref_json(item) for item in resources.roots]
        from generative_agents.ga_protocol.packages.reading import read_package_json
        try:
            read_package_json(Path(package), "run.json")
            result["source_kind"] = "run"
        except PackageError as exc:
            if str(exc) != "package member is missing: run.json":
                raise
            result["source_kind"] = read_package_json(Path(package), "manifest.json")["package_kind"]
        except FileNotFoundError:
            result["source_kind"] = read_package_json(Path(package), "manifest.json")["package_kind"]
        return result

    def _preview(self, session, resources, selected):
        items, conflicts, requirements = [], [], []
        for record in resources.resources:
            identity = _identity(record)
            existing = self._find(session, record.kind, record.key)
            status, diagnostics = "new", []
            if existing is not None:
                try:
                    # Complete local dependencies produce strong comparison, while
                    # unbound dependencies retain their original expected hashes.
                    local_set = self._local_set(session, record.kind, existing)
                    local = local_set.resources[0]
                    same = resource_content_hash(local, local_set) == resource_content_hash(record, resources)
                    if getattr(existing, "archived_at", None) is not None:
                        same = False
                except (ResourceExchangeError, ValueError):
                    same = False
                status = "reuse" if same else "conflict"
                if not same:
                    diagnostics.append({"code": "RESOURCE_KEY_CONFLICT", "message": "本机同 key 资源内容不同或已归档；不会覆盖"})
                    if identity in selected:
                        conflicts.append({**_ref_json(record), "message": diagnostics[-1]["message"]})
            for dependency in record.dependencies:
                if identity in selected:
                    requirements.append({**_ref_json(dependency), "required_by": _ref_json(record),
                                         "status": "included" if _identity(dependency) in selected else "pending"})
            if record.kind == "model":
                diagnostics.append({"code": "MODEL_CREDENTIALS_REQUIRED", "message": "导入后请确认本机服务地址并配置凭据"})
            items.append({**_ref_json(record), "name": record.name, "description": record.description,
                          "skill_kind": record.definition.get("skill_kind") if record.kind == "skill" else None,
                          "status": status, "selected": identity in selected,
                          "attachment_count": len(record.attachments),
                          "dependencies": [_ref_json(item) for item in record.dependencies],
                          "diagnostics": diagnostics})
        return {"resources": items, "requirements": requirements, "conflicts": conflicts,
                "can_import": bool(selected) and not conflicts}

    def import_resources(self, package, selections, *, include_dependencies=None):
        resources = self._load(package)
        selected = self._selection(resources, selections, include_dependencies)
        if not selected:
            raise ResourceExchangeError("请至少选择一个资源")
        try:
            with self.database.session_factory.begin() as session:
                preview = self._preview(session, resources, selected)
                if preview["conflicts"]:
                    raise ResourceExchangeError("资源 key 冲突，整次导入已取消", code="RESOURCE_KEY_CONFLICT", status_code=409, details=preview["conflicts"])
                records = [item for item in resources.resources if _identity(item) in selected]
                local_ids, imported, reused = {}, [], []
                # Allocate every identity before materializing relationships.
                for record in records:
                    existing = self._find(session, record.kind, record.key)
                    local_ids[_identity(record)] = existing.id if existing is not None else str(uuid4())
                asset_ids = self._store_assets(session, resources, records)
                for record in sorted(records, key=lambda item: (item.kind == "crowd", item.kind, item.key)):
                    existing = self._find(session, record.kind, record.key)
                    row_id = local_ids[_identity(record)]
                    item = {**_ref_json(record), "id": row_id, "name": record.name}
                    if existing is not None:
                        reused.append(item)
                        continue
                    self._insert(session, record, row_id, resources, local_ids, asset_ids)
                    private_files = {}
                    if record.kind == "skill":
                        prefix = PurePosixPath(record.definition["entrypoint"]).parent
                        private_files = {PurePosixPath(path).relative_to(prefix).as_posix(): base64.b64encode(resources.files[path]).decode("ascii")
                                         for path in record.attachments if path != record.definition["entrypoint"]
                                         and not PurePosixPath(path).relative_to(prefix).as_posix().startswith("scripts/")}
                    session.add(StudioResourceExchangeState(
                        resource_kind=record.kind, resource_id=row_id,
                        dependencies_json=[{**_ref_json(dependency), "resource_id": local_ids.get(_identity(dependency))}
                                           for dependency in record.dependencies],
                        extra_files_json=private_files,
                    ))
                    imported.append(item)
                session.flush()
                self._resolve_imported_dependencies(session, resources, local_ids)
                return {"imported": imported, "reused": reused,
                        "pending_dependencies": [{**item, "required_by": _ref_json(record)}
                                                 for record in records
                                                 for item in pending_resource_dependencies(session, record.kind, local_ids[record.identity])]}
        except IntegrityError as exc:
            raise ResourceExchangeError("公共资源已被其他操作修改，请重新预览后导入", code="RESOURCE_IMPORT_CHANGED", status_code=409) from exc

    def _local_set(self, session, kind, row):
        files, records, seen = {}, [], set()
        pending = [(kind, row)]
        while pending:
            current_kind, current = pending.pop(0)
            record = self._record(session, current_kind, current, files)
            if record.identity in seen:
                continue
            seen.add(record.identity)
            records.append(record)
            state = session.get(StudioResourceExchangeState, (current_kind, current.id))
            bindings = {(item["kind"], item["key"]): item for item in state.dependencies_json} if state else {}
            for dependency in record.dependencies:
                if dependency.identity in bindings and not bindings[dependency.identity].get("resource_id"):
                    continue
                target = self._find(session, dependency.kind, dependency.key)
                if target is not None and getattr(target, "archived_at", None) is None:
                    pending.append((dependency.kind, target))
        return ResourceSet(resources=records, files=files)

    def _resolve_imported_dependencies(self, session, resources, local_ids):
        """A separately imported dependency only binds when its expected bytes match."""
        hashes = {identity: resource_content_hash(resources.by_ref()[identity], resources) for identity in local_ids}
        for state in session.scalars(select(StudioResourceExchangeState)):
            dependencies = copy.deepcopy(state.dependencies_json)
            changed = False
            for dependency in dependencies:
                identity = (dependency["kind"], dependency["key"])
                if not dependency.get("resource_id") and dependency.get("expected_sha256") and hashes.get(identity) == dependency["expected_sha256"]:
                    dependency["resource_id"] = local_ids[identity]
                    changed = True
            if changed:
                state.dependencies_json = dependencies
                if state.resource_kind == "crowd":
                    crowd = session.get(StudioCrowd, state.resource_id)
                    if crowd is not None:
                        crowd.agent_ids_json = [item["resource_id"] for item in dependencies if item.get("resource_id")]
                        crowd.content_hash = _digest({"content": crowd.agent_ids_json})
                        crowd.row_version += 1
        session.flush()

    def _store_assets(self, session, resources, records):
        result = {}
        for path in sorted({path for record in records if record.kind != "skill" for path in record.attachments}):
            data = resources.files[path]
            digest = hashlib.sha256(data).hexdigest()
            row = session.scalar(select(Asset).where(Asset.sha256 == digest))
            if row is None:
                row = Asset(id=str(uuid4()), sha256=digest, logical_name=PurePosixPath(path).name,
                            media_type=_media_type(data, path), size_bytes=len(data), relative_path="", content_blob=data)
                session.add(row)
                session.flush()
            elif row.content_blob is None:
                row.content_blob = data
            result[path] = row.id
        return result

    def _insert(self, session, record, row_id, resources, local_ids, asset_ids):
        definition = copy.deepcopy(record.definition)
        common = {"id": row_id, "name": record.name or record.key, "description": record.description}
        if record.kind == "agent":
            definition.update(agent_key=record.key, name=common["name"])
            for role in ("portrait", "sprite"):
                path = definition.pop(f"{role}_asset", None)
                if path:
                    AssetService._validate_agent_png(resources.files[path], kind=role)
                definition[f"{role}_asset_id"] = asset_ids.get(path)
            definition = StudioAgentDefinition.model_validate(definition).model_dump(mode="json")
            row = StudioAgent(**common, agent_key=record.key, definition_json=definition, content_hash=_digest({"content": definition}))
        elif record.kind == "crowd":
            ids = [local_ids[_identity(_ref(item))] for item in definition.get("members") or [] if _identity(_ref(item)) in local_ids]
            row = StudioCrowd(**common, crowd_key=record.key, agent_ids_json=ids, content_hash=_digest({"content": ids}))
        elif record.kind == "skill":
            entrypoint = definition["entrypoint"]
            prefix = PurePosixPath(entrypoint).parent
            markdown = resources.files[entrypoint].decode("utf-8")
            private = {PurePosixPath(path).relative_to(prefix).as_posix(): resources.files[path]
                       for path in record.attachments if path != entrypoint}
            scripts = {path: content.decode("utf-8") for path, content in private.items() if path.startswith("scripts/")}
            parsed = skill_document(record, resources)
            actual_children = {ResourceRef(kind="skill", key=key).key for key in parsed.children}
            declared = {item.key for item in record.dependencies if item.kind == "skill"}
            if actual_children != declared:
                raise ResourceExchangeError(f"Skill 依赖声明与正文不一致：{record.key}")
            row = StudioSkill(id=row_id, skill_key=record.key, description=parsed.description,
                              kind=definition["skill_kind"], markdown=markdown,
                              scripts_json=scripts, children_json=list(parsed.children),
                              content_hash=DatabaseSkillRegistry._content_hash(markdown, scripts,
                                  extra_files={path: content for path, content in private.items() if not path.startswith("scripts/")}), is_builtin=False)
        elif record.kind in {"model", "evaluator"}:
            model, key_field = _MODELS[record.kind]
            row = model(**common, **{key_field: record.key}, config_json=definition, content_hash=_digest({"content": definition}))
        elif record.kind == "spatial_asset":
            contract = SpatialAssetContract.model_validate(definition).model_dump(mode="json")
            row = SpatialAssetDefinition(**common, asset_key=record.key, asset_kind=contract["kind"],
                                         contract_json=contract, contract_hash=_digest(contract), is_builtin=False)
        else:
            definition.update(world_key=record.key, world_name=common["name"])
            world = definition.get("definition") or {}
            for source in (world.get("editor_v2") or {}).get("material_sources") or []:
                if source.get("kind") == "BUNDLED":
                    path = source.pop("bundled_path", None)
                    source.update(kind="UPLOADED", asset_id=asset_ids[path])
            scene = world.get("spatial_scene") or {}
            contracts = (world.get("editor") or {}).get("spatial_assets") or {}
            mapping = {}
            for key, value in contracts.items():
                existing = self._find(session, "spatial_asset", key)
                contract = SpatialAssetContract.model_validate(value).model_dump(mode="json")
                if existing is not None:
                    local_contract = SpatialAssetContract.model_validate(existing.contract_json).model_dump(mode="json")
                    self._spatial_images(session, local_contract, {}, set())
                    if existing.archived_at is not None or local_contract != contract:
                        raise ResourceExchangeError(f"地图空间素材 key 内容冲突：{key}", code="RESOURCE_KEY_CONFLICT", status_code=409)
                if existing is None:
                    existing = SpatialAssetDefinition(id=str(uuid4()), asset_key=key, name=contract["name"],
                                                      description=contract.get("summary", ""), asset_kind=contract["kind"],
                                                      contract_json=contract, contract_hash=_digest(contract), is_builtin=False)
                    session.add(existing)
                    session.flush()
                mapping[key] = existing.id
            if scene:
                world["spatial_scene"] = _replace_paths(scene, mapping)
                world.setdefault("editor", {})["spatial_assets"] = {mapping[key]: value for key, value in contracts.items()}
            parsed = normalize_public_world(WorldConfig.model_validate(definition))
            definition = parsed.model_dump(mode="json", exclude_none=False)
            row = WorldMap(**common, map_key=record.key, world_json=definition,
                           world_hash=world_hash(parsed), validation_json=None)
        session.add(row)
        session.flush()


__all__ = ["ResourceExchangeError", "ResourceExchangeService", "resource_extra_files",
           "require_resolved_resource_dependencies", "bind_saved_resource_dependencies",
           "pending_resource_dependencies", "delete_resource_exchange_state"]
