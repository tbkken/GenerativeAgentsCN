"""Temporary execution views assembled from shared resources and bindings."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from collections import OrderedDict
from threading import RLock

from pydantic import ValidationError

from generative_agents.ga_protocol.packages.io import PackageError, atomic_write_json, checked_package_path, read_json
from generative_agents.ga_protocol.packages.resources import (
    ResourceRef, ResourceRecord, ResourceSet, object_skill_keys, read_resource_index,
    resource_attachment_paths, write_resource_set, refresh_resource_dependency_hashes,
)
from generative_agents.ga_protocol.packages.semantic_index import build_semantic_index
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest, SkillPackageEntry, SkillPackageRegistry
from generative_agents.ga_protocol.schemas.resources import ExperimentAssembly, ExperimentPlacement, ResourceIndex
from generative_agents.ga_protocol.packages.reading import file_fingerprint, read_package_json

_documents = OrderedDict()
_document_lock = RLock()


def read_experiment_documents(root: Path):
    """Keep parsed immutable read inputs; callers only copy selected content."""
    root = checked_package_path(root)
    header_path = root if root.is_file() else root / 'manifest.json'
    initial = file_fingerprint(header_path)
    header = ExperimentManifest.model_validate(read_package_json(root, 'manifest.json'))
    if file_fingerprint(header_path) != initial:
        raise PackageError('experiment header changed while reading')
    relatives = ('manifest.json', header.entrypoints.resources, header.entrypoints.assembly)
    signature = (file_fingerprint(root),) if root.is_file() else tuple(file_fingerprint(root / name) for name in relatives)
    if signature[0] != initial:
        raise PackageError('experiment header changed while reading')
    with _document_lock:
        cached = _documents.get(root)
        if cached and cached[0] == signature:
            _documents.move_to_end(root)
            return cached[1]
    documents = (header, ResourceIndex.model_validate(read_package_json(root, header.entrypoints.resources)),
                 ExperimentAssembly.model_validate(read_package_json(root, header.entrypoints.assembly)))
    after = (file_fingerprint(root),) if root.is_file() else tuple(file_fingerprint(root / name) for name in relatives)
    if after != signature:
        raise PackageError('experiment changed while reading resource definitions')
    with _document_lock:
        _documents[root] = (signature, documents)
        _documents.move_to_end(root)
        while len(_documents) > 8:
            _documents.popitem(last=False)
    return documents


def read_experiment_assembly(root: str | Path) -> ExperimentAssembly:
    root = checked_package_path(Path(root))
    manifest = ExperimentManifest.model_validate(read_json(root / "manifest.json"))
    return ExperimentAssembly.model_validate(read_json(root / manifest.entrypoints.assembly))


def assemble_experiment_definition(manifest: ExperimentManifest, resources: ResourceSet,
                                   assembly: ExperimentAssembly) -> dict:
    world_record = resources.get(assembly.map)
    if world_record.kind != "map":
        raise PackageError("experiment map selection must identify a Map")
    world = dict(copy.deepcopy(world_record.definition), world_key=world_record.key, world_name=world_record.name)
    agents = []
    for placement in assembly.placements:
        record = resources.get(placement.agent)
        if record.kind != "agent":
            raise PackageError("experiment placement must identify an Agent")
        agents.append(dict(copy.deepcopy(record.definition), agent_key=record.key, name=record.name,
                           coord=list(placement.coord), spatial=copy.deepcopy(placement.spatial)))
    crowds = []
    for reference in assembly.crowds:
        record = resources.get(reference)
        if record.kind != "crowd":
            raise PackageError("experiment crowd selection must identify a Crowd")
        crowds.append({"crowd_key": record.key, "name": record.name, "description": record.description,
                       "agent_keys": [ResourceRef.model_validate(member).key for member in record.definition["members"]]})
    models = {}
    for purpose, reference in assembly.models.items():
        record = resources.get(reference)
        if record.kind != "model" or purpose not in record.definition:
            raise PackageError(f"selected model resource has no {purpose} configuration")
        models[purpose] = copy.deepcopy(record.definition[purpose])
    engine = dict(assembly.engine)
    if "brain_skill" in engine:
        raise PackageError("Brain selection belongs only to experiment assembly.brain")
    engine["brain_skill"] = assembly.brain.key
    return {
        "schema_version": 1, "experiment": manifest.experiment.model_dump(mode="json"),
        "world": world, "agents": agents, "crowds": crowds, "models": models,
        "engine": engine, "simulation": copy.deepcopy(assembly.simulation),
        "results": copy.deepcopy(assembly.results),
        "evaluation": {"evaluators": [copy.deepcopy(resources.get(reference).definition) for reference in assembly.evaluators]},
    }


def _experiment_definition(root: Path, *, _reader=None, summary_only=False) -> tuple[dict, dict]:
    root = Path(root)
    read_document = _reader or (lambda relative: read_json(root / relative))
    try:
        if _reader is None:
            manifest, index, assembly = read_experiment_documents(root)
        else:
            manifest = ExperimentManifest.model_validate(read_document("manifest.json"))
            index = ResourceIndex.model_validate(read_document(manifest.entrypoints.resources))
            assembly = ExperimentAssembly.model_validate(read_document(manifest.entrypoints.assembly))
    except (ValidationError, KeyError, OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"invalid experiment package: {exc}") from exc
    resources = ResourceSet(index.resources, {}, index.roots)
    if summary_only:
        world = resources.get(assembly.map)
        definition = {'world': {'world_key': world.key, 'world_name': world.name},
                      'agents': [{'agent_key': placement.agent.key,
                                  'enabled': resources.get(placement.agent).definition.get('enabled', True)}
                                 for placement in assembly.placements],
                      'simulation': copy.deepcopy(assembly.simulation)}
    else:
        definition = assemble_experiment_definition(manifest, resources, assembly)
    return manifest.model_dump(mode="json"), definition


def experiment_overview(root: Path) -> tuple[dict, dict, dict]:
    """Authoring settings and counts, without any map or actor payload."""
    manifest, index, assembly = read_experiment_documents(Path(root))
    resources = ResourceSet(index.resources, {}, index.roots)
    world = resources.get(assembly.map)
    models = {purpose: copy.deepcopy(resources.get(ref).definition[purpose])
              for purpose, ref in assembly.models.items()}
    definition = {'schema_version': 1, 'experiment': manifest.experiment.model_dump(mode='json'),
                  'engine': {**copy.deepcopy(assembly.engine), 'brain_skill': assembly.brain.key},
                  'models': models, 'simulation': copy.deepcopy(assembly.simulation),
                  'results': copy.deepcopy(assembly.results),
                  'evaluation': {'evaluators': [copy.deepcopy(resources.get(ref).definition) for ref in assembly.evaluators]}}
    summary = {'agent_count': len(assembly.placements), 'crowd_count': len(assembly.crowds),
               'world_name': world.name, 'max_steps': assembly.simulation.get('max_steps'),
               'enabled_agent_count': sum(bool(resources.get(p.agent).definition.get('enabled', True)) for p in assembly.placements),
               'skill_bound_objects': 0}
    world_definition = world.definition.get('definition') or {}
    nodes = (world_definition.get('editor_v2') or {}).get('hierarchy_nodes') or []
    summary['skill_bound_objects'] = sum(len(node.get('skill_bindings') or []) for node in nodes if node.get('kind') == 'GAME_OBJECT')
    assets = (world_definition.get('editor') or {}).get('spatial_assets') or {}
    for placement in (world_definition.get('spatial_scene') or {}).get('placements') or []:
        contract = assets.get(str(placement.get('spatial_asset_id') or '')) or {}
        if contract.get('kind') == 'OBJECT':
            summary['skill_bound_objects'] += len(contract.get('skill_bindings') or [])
    return manifest.model_dump(mode='json'), definition, summary


def assemble_skill_registry(resources: ResourceSet, assembly: ExperimentAssembly) -> SkillPackageRegistry:
    objects = object_skill_keys(resources.get(assembly.map).definition)
    entries = []
    for record in sorted(resources.resources, key=lambda item: item.identity):
        if record.kind != "skill":
            continue
        path = record.definition["entrypoint"]
        entries.append(SkillPackageEntry(
            skill_id=record.key,
            kind="brain" if record.key == assembly.brain.key else "object" if record.key in objects else "sub_skill",
            editor_kind=record.definition["skill_kind"], path=path,
            dependencies=[ref.key for ref in record.dependencies],
            content_sha256=hashlib.sha256(resources.files[path]).hexdigest() if path in resources.files else None,
        ))
    return SkillPackageRegistry(brain_skill=assembly.brain.key, object_roots=sorted(objects), skills=entries)


def _record(kind, key, name, definition, *, old, dependencies=()):
    reference = ResourceRef(kind=kind, key=key)
    previous = old.get(reference.identity)
    return ResourceRecord(kind=kind, key=key, name=name, description=previous.description if previous else "",
                          definition=definition, dependencies=list(dependencies),
                          attachments=sorted(resource_attachment_paths(definition)))


def write_experiment_definition(root: str | Path, definition: dict, *, resource_set: ResourceSet | None = None,
                                model_selections: dict[str, ResourceRef] | None = None) -> None:
    """Write resources and assembly, deriving semantics from current geometry.

    Callers publish integrity after their entire staged edit. Referenced files
    already uploaded into this package are adopted into the resource inventory.
    """
    root = checked_package_path(Path(root))
    manifest = ExperimentManifest.model_validate(read_json(root / "manifest.json"))
    prior = resource_set
    if prior is None:
        prior = read_resource_index(root, manifest.entrypoints.resources) if (root / manifest.entrypoints.resources).exists() else ResourceSet()
    old = prior.by_ref()
    files = dict(prior.files)
    previous_assembly = (read_experiment_assembly(root) if (root / manifest.entrypoints.assembly).exists() else None)
    records = [record.model_copy(deep=True) for record in prior.resources if record.kind in {"skill", "spatial_asset"}]
    world = copy.deepcopy(definition.get("world"))
    if not isinstance(world, dict):
        raise PackageError("experiment definition must include its Map")
    world.pop("schema_version", None)
    world.pop("map_id", None)
    world.pop("map_snapshot_hash", None)
    previous_map = old.get(("map", str(world.get("world_key") or "")))
    previous_world = (dict(previous_map.definition, world_key=previous_map.key, world_name=previous_map.name)
                      if previous_map is not None else None)
    if world != previous_world:
        world, _ = build_semantic_index(world)
    world_key, world_name = world.pop("world_key"), world.pop("world_name")
    map_record = _record("map", world_key, world_name, world, old=old,
                         dependencies=[ResourceRef(kind="skill", key=key) for key in sorted(object_skill_keys(world))])
    records.append(map_record)
    placements = []
    for raw in definition.get("agents", []):
        core = copy.deepcopy(raw)
        key, name = core.pop("agent_key"), core.pop("name")
        try:
            coord, spatial = core.pop("coord"), core.pop("spatial")
        except KeyError as exc:
            raise PackageError(f"Agent {key} requires an explicit experiment placement") from exc
        records.append(_record("agent", key, name, core, old=old))
        placements.append(ExperimentPlacement(agent=ResourceRef(kind="agent", key=key), coord=coord, spatial=spatial))
    crowd_refs = []
    for raw in definition.get("crowds", []):
        members = [ResourceRef(kind="agent", key=key) for key in raw["agent_keys"]]
        record = _record("crowd", raw["crowd_key"], raw["name"],
                         {"members": [member.model_dump(mode="json", exclude_none=True) for member in members]},
                         old=old, dependencies=members)
        record.description = str(raw.get("description", record.description))
        records.append(record)
        crowd_refs.append(ResourceRef(kind="crowd", key=record.key))
    selected_models = {}
    model_records = {}
    for purpose, config in definition.get("models", {}).items():
        if purpose not in {"chat", "embedding"}:
            raise PackageError(f"unsupported model purpose: {purpose}")
        reference = (model_selections or {}).get(purpose)
        if reference is not None:
            reference = ResourceRef.model_validate(reference)
            if reference.kind != "model" or reference.identity not in old:
                raise PackageError(f"explicit {purpose} model selection is missing from resource content")
        else:
            reference = previous_assembly.models.get(purpose) if previous_assembly else None
        if reference is None:
            matches = [record for record in prior.resources if record.kind == "model" and record.definition.get(purpose) == config]
            if len(matches) > 1:
                raise PackageError(f"ambiguous {purpose} model selection; provide one selected preset per purpose")
            reference = ResourceRef(kind="model", key=matches[0].key) if matches else ResourceRef(kind="model", key=f"model-{purpose}")
        previous = old.get(reference.identity)
        if reference.identity not in model_records:
            model_records[reference.identity] = (previous.model_copy(deep=True) if previous else ResourceRecord(
                kind="model", key=reference.key, name=f"{purpose} model", definition={}))
        model_records[reference.identity].definition[purpose] = copy.deepcopy(config)
        selected_models[purpose] = reference.model_copy(update={"expected_sha256": None})
    records.extend(model_records.values())
    evaluator_refs = []
    for index, raw in enumerate((definition.get("evaluation") or {}).get("evaluators", []), start=1):
        key = str(raw.get("evaluator_key") or raw.get("key") or f"evaluator-{index}")
        record = _record("evaluator", key, str(raw.get("name") or key), copy.deepcopy(raw), old=old)
        records.append(record)
        evaluator_refs.append(ResourceRef(kind="evaluator", key=key))
    engine = dict(definition.get("engine") or {})
    brain = engine.pop("brain_skill", None)
    if not brain:
        raise PackageError("experiment requires an explicit Brain selection")
    assembly = ExperimentAssembly(map=ResourceRef(kind="map", key=world_key), brain=ResourceRef(kind="skill", key=brain),
        placements=placements, crowds=crowd_refs, models=selected_models, simulation=copy.deepcopy(definition.get("simulation") or {}),
        engine=engine, results=copy.deepcopy(definition.get("results") or {}), evaluators=evaluator_refs)
    owned = {path for record in records for path in record.attachments}
    for path in owned - set(files):
        source = checked_package_path(root / path)
        source.relative_to(root)
        if not source.is_file():
            raise PackageError(f"experiment resource attachment is missing: {path}")
        files[path] = source.read_bytes()
    resources = ResourceSet(records, {path: files[path] for path in sorted(owned)}, [])
    resources.roots = [assembly.map, assembly.brain, *(placement.agent for placement in placements),
                       *crowd_refs, *selected_models.values(), *evaluator_refs]
    resources.roots = list({reference.identity: reference for reference in resources.roots}.values())
    refresh_resource_dependency_hashes(resources)
    write_resource_set(root, resources, index_path=manifest.entrypoints.resources)
    atomic_write_json(root / manifest.entrypoints.assembly, assembly.model_dump(mode="json", exclude_none=True))
