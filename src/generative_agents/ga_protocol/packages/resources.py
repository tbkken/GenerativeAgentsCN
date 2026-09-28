"""One portable resource graph shared by config, experiment and Run packages.

Only this module maps the physical resource index to resource bytes. Studio IDs,
credentials and runtime state never become resource references.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_write_bytes, atomic_write_json, canonical_json_bytes,
    checked_package_path, open_package, read_json, verify_integrity, write_integrity_manifest,
)
from generative_agents.ga_protocol.schemas.manifests import (
    ConfigManifest, ExperimentManifest, RunManifest, validate_package_path,
)
from generative_agents.ga_protocol.schemas.resources import AgentCore, ResourceIndex, ResourceRecord, ResourceRef
from generative_agents.ga_protocol.schemas.experiment import ChatModelConfig, EmbeddingModelConfig, WorldConfig
from generative_agents.ga_protocol.skills.documents import SkillRegistry


@dataclass
class ResourceSet:
    resources: list[ResourceRecord] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    roots: list[ResourceRef] = field(default_factory=list)

    def by_ref(self) -> dict[tuple[str, str], ResourceRecord]:
        return {record.identity: record for record in self.resources}

    def get(self, reference: ResourceRef) -> ResourceRecord:
        try:
            return self.by_ref()[reference.identity]
        except KeyError as exc:
            raise PackageError(f"resource is missing: {reference.kind}/{reference.key}") from exc


_FORBIDDEN = {"map_id", "map_snapshot_hash", "revision_id", "brain_revision_id",
              "brain_revision_hash", "skill_revision_id", "source_revision_id", "secret_ref",
              "api_key", "api_token", "secret_id", "portrait_asset_id", "sprite_asset_id"}


def _no_external_references(value: object, location: str = "resource") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN or key.endswith("_revision_id"):
                raise PackageError(f"external or secret field is forbidden: {location}.{key}")
            _no_external_references(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _no_external_references(child, f"{location}[{index}]")


def object_skill_keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        bindings = value.get("skill_bindings")
        if isinstance(bindings, list):
            if len(bindings) > 1:
                raise PackageError("a Game Object binds one root Skill")
            from generative_agents.ga_protocol.schemas.object_skills import GameObjectSkillBinding
            for binding in bindings:
                parsed = GameObjectSkillBinding.model_validate(binding)
                result.add(parsed.skill_name)
        for child in value.values():
            result.update(object_skill_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(object_skill_keys(child))
    return result


def resource_attachment_paths(value: object) -> set[str]:
    """Discover file references in map/agent/spatial content, not arbitrary prose."""
    result: set[str] = set()
    path_fields = {"logical_path", "portrait_asset", "sprite_asset", "asset_path", "image_path", "bundled_path", "entrypoint"}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in path_fields and isinstance(child, str) and child:
                normalized = validate_package_path(child)
                if normalized != child:
                    raise PackageError(f"file reference must use a relative POSIX path: {child}")
                result.add(normalized)
            elif isinstance(child, (dict, list)):
                result.update(resource_attachment_paths(child))
    elif isinstance(value, list):
        for child in value:
            result.update(resource_attachment_paths(child))
    return result


def skill_document(record: ResourceRecord, resources: ResourceSet):
    entrypoint = validate_package_path(str(record.definition.get("entrypoint") or ""))
    if PurePosixPath(entrypoint).name != "SKILL.md":
        raise PackageError(f"Skill {record.key} entrypoint must be SKILL.md")
    try:
        markdown = resources.files[entrypoint].decode("utf-8-sig")
    except (KeyError, UnicodeError) as exc:
        raise PackageError(f"Skill {record.key} has no UTF-8 SKILL.md") from exc
    kind = record.definition.get("skill_kind")
    if kind not in {"atomic", "pack", "brain"}:
        raise PackageError(f"invalid Skill type: {kind}")
    parent = PurePosixPath(entrypoint).parent
    files = {}
    for name in record.attachments:
        try:
            relative = PurePosixPath(name).relative_to(parent).as_posix()
        except ValueError as exc:
            raise PackageError(f"Skill attachment is outside its directory: {name}") from exc
        if relative != "SKILL.md":
            files[relative] = resources.files[name]
    # Explicit bytes prevent the parser from inspecting the host filesystem.
    return SkillRegistry(Path("."))._parse(markdown, Path(record.key) / "SKILL.md", kind, file_contents=files)


def validate_resource_set(resources: ResourceSet, *, require_dependencies: bool = True) -> None:
    identities = [record.identity for record in resources.resources]
    if not identities:
        raise PackageError("resource set must contain at least one resource")
    if len(identities) != len(set(identities)):
        raise PackageError("resource kind/key pairs must be unique")
    by_ref = resources.by_ref()
    normalized_files: set[str] = set()
    for name, content in resources.files.items():
        normalized = validate_package_path(name)
        if name != normalized or name.casefold() in normalized_files:
            raise PackageError(f"ambiguous resource attachment path: {name}")
        normalized_files.add(name.casefold())
        if not isinstance(content, bytes):
            raise PackageError(f"resource attachment must contain bytes: {name}")
        if name in {"manifest.json", "run.json", "resources/index.json", "runtime/assembly.json", "integrity/sha256.json"}:
            raise PackageError(f"attachment collides with package metadata: {name}")
    owned_files: set[str] = set()
    for record in resources.resources:
        location = f"{record.kind}/{record.key}"
        _no_external_references(record.definition, location)
        if len(record.attachments) != len(set(record.attachments)):
            raise PackageError(f"duplicate attachment in {location}")
        missing_files = set(record.attachments) - set(resources.files)
        if missing_files:
            raise PackageError(f"attachments missing for {location}: {sorted(missing_files)}")
        owned_files.update(record.attachments)
        references = resource_attachment_paths(record.definition)
        if references - set(record.attachments):
            raise PackageError(f"undeclared attachment references in {location}: {sorted(references - set(record.attachments))}")
        for asset in record.definition.get("assets") or []:
            if not isinstance(asset, dict) or not isinstance(asset.get("logical_path"), str):
                raise PackageError(f"invalid asset metadata in {location}")
            path = asset["logical_path"]
            if path not in resources.files:
                raise PackageError(f"resource asset is missing: {path}")
            content = resources.files[path]
            if asset.get("asset_hash") != "sha256:" + hashlib.sha256(content).hexdigest():
                raise PackageError(f"resource asset hash mismatch: {path}")
            if asset.get("size") != len(content):
                raise PackageError(f"resource asset size mismatch: {path}")
        deps = {ref.identity for ref in record.dependencies}
        if len(deps) != len(record.dependencies):
            raise PackageError(f"duplicate dependencies in {location}")
        if require_dependencies and deps - set(by_ref):
            raise PackageError(f"resource dependencies missing for {location}: {sorted(deps - set(by_ref))}")
        try:
            if record.kind in {"agent", "crowd", "model"} and not record.name.strip():
                raise PackageError(f"{location} requires a display name")
            if record.kind == "agent":
                AgentCore.model_validate(record.definition)
            elif record.kind == "map":
                WorldConfig.model_validate(dict(record.definition, world_key=record.key, world_name=record.name))
                needed = {("skill", key) for key in object_skill_keys(record.definition)}
                if not needed <= deps:
                    raise PackageError(f"map object Skill dependencies are undeclared: {sorted(needed - deps)}")
            elif record.kind == "crowd":
                if set(record.definition) != {"members"} or not isinstance(record.definition["members"], list):
                    raise PackageError("Crowd content must declare members")
                members = [ResourceRef.model_validate(value) for value in record.definition["members"]]
                if any(ref.kind != "agent" for ref in members) or len({ref.identity for ref in members}) != len(members):
                    raise PackageError("Crowd members must be unique Agent references")
                if {ref.identity for ref in members} != deps:
                    raise PackageError("Crowd dependencies must exactly match its members")
            elif record.kind == "skill":
                if set(record.definition) != {"skill_kind", "entrypoint"}:
                    raise PackageError("Skill content must declare only skill_kind and entrypoint")
                document = skill_document(record, resources)
                if document.name != record.key:
                    raise PackageError("Skill key and frontmatter name must match")
                if deps != {("skill", key) for key in document.children}:
                    raise PackageError(f"Skill {record.key} dependency index disagrees with its Markdown")
            elif record.kind == "model":
                if not record.definition or set(record.definition) - {"chat", "embedding"}:
                    raise PackageError("model content must contain chat and/or embedding configuration")
                for purpose, config in record.definition.items():
                    TypeAdapter(ChatModelConfig if purpose == "chat" else EmbeddingModelConfig).validate_python(config)
                    from urllib.parse import urlsplit
                    address = urlsplit(str(config.get("base_url") or ""))
                    if address.username or address.password or address.query or address.fragment:
                        raise PackageError("model service URL cannot contain credentials, query parameters or fragments")
            elif record.kind == "spatial_asset":
                from generative_agents.ga_protocol.schemas.spatial_assets import SpatialAssetContract
                SpatialAssetContract.model_validate(record.definition)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, PackageError):
                raise
            raise PackageError(f"invalid {location}: {exc}") from exc
    if set(resources.files) != owned_files:
        raise PackageError(f"unowned resource attachments: {sorted(set(resources.files) - owned_files)}")
    if any(ref.identity not in by_ref for ref in resources.roots):
        raise PackageError("selected resource root is missing")
    if len({ref.identity for ref in resources.roots}) != len(resources.roots):
        raise PackageError("selected resource roots must be unique")
    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()
    def visit(key: tuple[str, str]) -> None:
        if key in visiting:
            raise PackageError(f"cyclic resource dependency: {key[0]}/{key[1]}")
        if key in visited or key not in by_ref:
            return
        visiting.add(key)
        for ref in by_ref[key].dependencies:
            visit(ref.identity)
        visiting.remove(key)
        visited.add(key)
    for key in by_ref:
        visit(key)
    for record in resources.resources:
        for reference in record.dependencies:
            if reference.expected_sha256 and reference.identity in by_ref:
                actual = resource_content_hash(by_ref[reference.identity], resources)
                if actual != reference.expected_sha256:
                    raise PackageError(f"resource dependency content conflict: {reference.kind}/{reference.key}")
    for reference in resources.roots:
        if reference.expected_sha256 and resource_content_hash(by_ref[reference.identity], resources) != reference.expected_sha256:
            raise PackageError(f"selected resource content conflict: {reference.kind}/{reference.key}")


def resource_content_hash(record: ResourceRecord, resources: ResourceSet, *, _visiting=None, _cache=None) -> str:
    """Hash owned content and dependency identity, never host IDs or archive paths."""
    visiting = set() if _visiting is None else _visiting
    cache = {} if _cache is None else _cache
    if record.identity in cache:
        return cache[record.identity]
    if record.identity in visiting:
        raise PackageError(f"cyclic resource dependency: {record.kind}/{record.key}")
    visiting.add(record.identity)
    definition = copy.deepcopy(record.definition)
    files = {path: hashlib.sha256(resources.files[path]).hexdigest() for path in record.attachments}
    if record.kind == "map":
        # A public map and its experiment copy have the same content identity:
        # tile semantics are derived, never an independent author revision.
        from generative_agents.ga_protocol.packages.semantic_index import build_semantic_index
        definition = WorldConfig.model_validate(dict(definition, world_key=record.key,
                                                     world_name=record.name)).model_dump(mode="json")
        definition.pop("world_key", None)
        definition.pop("world_name", None)
        if isinstance(definition.get("definition", {}).get("tiles"), list):
            world, _ = build_semantic_index(dict(definition, world_key=record.key, world_name=record.name))
            world.pop("world_key", None)
            world.pop("world_name", None)
            definition = world
        geometry = definition.get("definition", {})
        # Author imports make the implicit tile unit explicit. That default
        # cannot turn an unchanged imported map into a content conflict.
        geometry.setdefault("size_unit", "TILE")
        editor = geometry.get("editor")
        if isinstance(editor, dict):
            if not editor.get("spatial_assets"):
                editor.pop("spatial_assets", None)
            if not editor:
                geometry.pop("editor", None)
    elif record.kind == "agent":
        definition = AgentCore.model_validate(definition).model_dump(mode="json")
    elif record.kind == "model":
        definition = {purpose: TypeAdapter(ChatModelConfig if purpose == "chat" else EmbeddingModelConfig)
                      .validate_python(config).model_dump(mode="json") for purpose, config in definition.items()}
    elif record.kind == "crowd":
        definition["members"] = [{"kind": value["kind"], "key": value["key"]} for value in definition["members"]]
    if record.kind == "skill":
        base = PurePosixPath(record.definition["entrypoint"]).parent
        files = {PurePosixPath(path).relative_to(base).as_posix(): digest for path, digest in files.items()}
        definition["entrypoint"] = "SKILL.md"
    else:
        # Image transport paths are locators. Their bytes and all references to
        # those bytes determine resource identity across machines and packages.
        def normalize_paths(value):
            if isinstance(value, dict):
                return {key: normalize_paths(child) for key, child in value.items()}
            if isinstance(value, list):
                return [normalize_paths(child) for child in value]
            return "sha256:" + files[value] if isinstance(value, str) and value in files else value
        definition = normalize_paths(definition)
        if record.kind == "map" and isinstance(definition.get("assets"), list):
            definition["assets"] = sorted(definition["assets"], key=lambda item: item["logical_path"])
        files = {digest: digest for digest in files.values()}
    by_ref = resources.by_ref()
    dependencies = []
    for ref in sorted(record.dependencies, key=lambda item: item.identity):
        child_hash = (resource_content_hash(by_ref[ref.identity], resources, _visiting=visiting, _cache=cache)
                      if ref.identity in by_ref else ref.expected_sha256)
        dependencies.append({"kind": ref.kind, "key": ref.key, "content_sha256": child_hash})
    visiting.remove(record.identity)
    digest = hashlib.sha256(canonical_json_bytes({
        "kind": record.kind, "key": record.key, "name": record.name, "description": record.description,
        "definition": definition,
        "dependencies": dependencies,
        "files": files,
    })).hexdigest()
    cache[record.identity] = digest
    return digest


def refresh_resource_dependency_hashes(resources: ResourceSet) -> ResourceSet:
    """Refresh internal expectations after an explicit local draft edit.

    Import validation must use the original expectations before this operation.
    Unresolved references retain their sender's expected content identity.
    """
    by_ref = resources.by_ref()
    cache = {}
    for record in resources.resources:
        record.dependencies = [reference.model_copy(update={
            "expected_sha256": resource_content_hash(by_ref[reference.identity], resources, _cache=cache)})
            if reference.identity in by_ref else reference for reference in record.dependencies]
        if record.kind == "crowd":
            dependencies = {reference.identity: reference for reference in record.dependencies}
            record.definition["members"] = [dependencies[ResourceRef.model_validate(member).identity]
                .model_dump(mode="json", exclude_none=True) for member in record.definition["members"]]
    return resources


def select_resources(resources: ResourceSet, roots: Sequence[ResourceRef], *, include_dependencies: bool = True) -> ResourceSet:
    selected: set[tuple[str, str]] = set()
    normalized_roots = [ResourceRef.model_validate(value) for value in roots]
    normalized_roots = list({ref.identity: ref for ref in normalized_roots}.values())
    pending = list(normalized_roots)
    while pending:
        ref = pending.pop()
        if ref.identity in selected:
            continue
        record = resources.get(ref)
        selected.add(ref.identity)
        if include_dependencies:
            pending.extend(record.dependencies)
    records = [record.model_copy(deep=True) for record in resources.resources if record.identity in selected]
    by_ref = resources.by_ref()
    for record in records:
        record.dependencies = [ref.model_copy(update={"expected_sha256": resource_content_hash(by_ref[ref.identity], resources)})
                               if ref.identity in by_ref else ref for ref in record.dependencies]
    paths = {path for record in records for path in record.attachments}
    result = ResourceSet(records, {path: resources.files[path] for path in sorted(paths)},
                         normalized_roots)
    validate_resource_set(result, require_dependencies=include_dependencies)
    return result


def read_resource_index(root: Path, index_path: str) -> ResourceSet:
    root = checked_package_path(root)
    try:
        index = ResourceIndex.model_validate(read_json(root / validate_package_path(index_path)))
    except ValidationError as exc:
        raise PackageError(f"invalid shared resource index: {exc}") from exc
    paths = {path for record in index.resources for path in record.attachments}
    files = {}
    for path in sorted(paths):
        source = checked_package_path(root / path)
        try:
            source.relative_to(root)
            files[path] = source.read_bytes()
        except (ValueError, OSError) as exc:
            raise PackageError(f"resource attachment is missing or unsafe: {path}") from exc
    return ResourceSet(index.resources, files, index.roots)


def read_experiment_resource_set(root: str | Path, *, verify_hashes: bool = False) -> ResourceSet:
    root = checked_package_path(Path(root))
    manifest = ExperimentManifest.model_validate(read_json(root / "manifest.json"))
    if verify_hashes:
        verify_integrity(root)
    return read_resource_index(root, manifest.entrypoints.resources)


def read_resource_set(path: str | Path, *, verify_hashes: bool = True) -> ResourceSet:
    source = Path(path)
    with open_package(source) as root:
        if (root / "run.json").is_file():
            from generative_agents.ga_protocol.packages.validation import validate_run_integrity
            run = validate_run_integrity(root, sealed=verify_hashes and source.is_file())
            root = root / run.experiment.path
        manifest_data = read_json(root / "manifest.json")
        kind = manifest_data.get("package_kind") if isinstance(manifest_data, dict) else None
        try:
            manifest = (ConfigManifest if kind == "config" else ExperimentManifest).model_validate(manifest_data)
        except ValidationError as exc:
            raise PackageError(f"unsupported or invalid package manifest: {exc}") from exc
        if verify_hashes:
            verify_integrity(root)
        resources = read_resource_index(root, manifest.entrypoints.resources)
        validate_resource_set(resources, require_dependencies=kind != "config")
        if kind == "exp":
            from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
            validate_experiment_directory(root, verify_hashes=False)
        return resources


def write_resource_set(root: str | Path, resources: ResourceSet, *, index_path: str = "resources/index.json",
                       require_dependencies: bool = True) -> None:
    root = checked_package_path(Path(root))
    validate_resource_set(resources, require_dependencies=require_dependencies)
    index_path = validate_package_path(index_path)
    if index_path in resources.files:
        raise PackageError("resource index collides with an attachment")
    previous_paths: set[str] = set()
    if (root / index_path).is_file():
        previous = ResourceIndex.model_validate(read_json(root / index_path))
        previous_paths = {path for record in previous.resources for path in record.attachments}
    for name, content in sorted(resources.files.items()):
        target = checked_package_path(root / name)
        target.relative_to(root)
        atomic_write_bytes(target, content)
    index = ResourceIndex(resources=sorted(resources.resources, key=lambda record: record.identity), roots=resources.roots)
    atomic_write_json(root / index_path, index.model_dump(mode="json"))
    for name in sorted(previous_paths - set(resources.files)):
        target = checked_package_path(root / name)
        target.relative_to(root)
        target.unlink(missing_ok=True)


def write_config_package(destination: str | Path, resources: ResourceSet, *, name: str = "Configuration",
                         config_id: str | None = None, created_at: datetime | None = None) -> Path:
    root = checked_package_path(Path(destination))
    if root.exists() and any(root.iterdir()):
        raise PackageError(f"configuration destination is not empty: {root}")
    validate_resource_set(resources, require_dependencies=False)
    root.mkdir(parents=True, exist_ok=True)
    manifest = ConfigManifest(config_id=config_id or str(uuid4()), name=name, created_at=created_at or datetime.now(UTC))
    write_resource_set(root, resources, index_path=manifest.entrypoints.resources, require_dependencies=False)
    atomic_write_json(root / "manifest.json", manifest.model_dump(mode="json"))
    write_integrity_manifest(root)
    return root
