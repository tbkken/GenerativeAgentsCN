"""Current-protocol resource sharing never reconstructs source-machine locators."""
from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from generative_agents.ga_protocol.packages.definition import _experiment_definition, write_experiment_definition
from generative_agents.ga_protocol.packages.io import PackageError, read_json, seal_directory, write_integrity_manifest
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceRef, ResourceSet, read_resource_set, resource_content_hash,
    select_resources, validate_resource_set, write_config_package,
)
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest
from generative_agents.ga_runtime.lifecycle.package import load_experiment_directory
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource


def _skills() -> ResourceSet:
    child_path = "skills/items/read-clock/SKILL.md"
    parent_path = "skills/items/daily-brain/SKILL.md"
    script_path = "skills/items/read-clock/scripts/read.py"
    template_path = "skills/items/read-clock/templates/example.bin"
    return ResourceSet([
        ResourceRecord(kind="skill", key="read-clock", name="read-clock", description="Read the clock",
                       definition={"skill_kind": "atomic", "entrypoint": child_path},
                       attachments=[child_path, script_path, template_path]),
        ResourceRecord(kind="skill", key="daily-brain", name="daily-brain", description="Daily decisions",
                       definition={"skill_kind": "brain", "entrypoint": parent_path},
                       dependencies=[ResourceRef(kind="skill", key="read-clock")], attachments=[parent_path]),
    ], {
        child_path: b"---\nname: read-clock\ndescription: Read the clock\n---\nRead the current time.\n",
        script_path: b"print('time')\n", template_path: b"\x00\xff\x03",
        parent_path: b"---\nname: daily-brain\ndescription: Daily decisions\n---\nUse $read-clock, then WAIT.\n",
    })


def _definition() -> dict:
    address = ["Home", "Ground floor", "Study", "Desk"]
    return {"experiment": {"key": "shared-test", "name": "Shared test", "timezone": "Asia/Shanghai"},
            "engine": {"algorithm_version": "ga-cn-v1", "brain_skill": "daily-brain"},
            "simulation": {"start_time": "2026-09-22T08:00:00+08:00", "max_steps": 3},
            "models": {"chat": {"provider": "vllm", "base_url": "http://127.0.0.1:8888/v1", "model": "test-chat"},
                       "embedding": {"provider": "openai_compatible", "base_url": "http://127.0.0.1:5002/v1", "model": "test-embedding"}},
            "world": {"world_key": "shared-home", "world_name": "Home", "assets": [], "definition": {
                "world": "Home", "size": [1, 1], "tile_address_keys": ["world", "sector", "arena", "game_object"],
                "tiles": [{"coord": [0, 0], "collision": False, "address": address}]}},
            "agents": [{"agent_key": "alice", "name": "Alice", "scratch": {"age": 30},
                        "coord": [0, 0], "spatial": {"address": {"initial_location": address},
                                                     "tree": {"Home": {"Ground floor": {"Study": ["Desk"]}}}}}],
            "crowds": [{"crowd_key": "friends", "name": "Friends", "description": "Shared team", "agent_keys": ["alice"]}]}


def _experiment(tmp_path: Path) -> Path:
    resources = _skills()
    sources = []
    for record in resources.resources:
        prefix = Path(record.definition["entrypoint"]).parent
        for name in record.attachments:
            relative = Path(name).relative_to(prefix)
            target = tmp_path / "author" / record.key / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resources.files[name])
        sources.append(SkillSource(record.key, "brain" if record.key == "daily-brain" else "sub_skill",
                                   tmp_path / "author" / record.key / "SKILL.md",
                                   tuple(ref.key for ref in record.dependencies), record.definition["skill_kind"]))
    return ExperimentPackageBuilder().build_directory(tmp_path / "experiment", definition=_definition(), skills=sources,
                                                       brain_skill="daily-brain")


def test_single_config_retains_missing_dependency_identity_and_exact_files(tmp_path: Path):
    source = _skills()
    selected = select_resources(source, [ResourceRef(kind="skill", key="daily-brain")], include_dependencies=False)
    dependency = selected.resources[0].dependencies[0]
    assert dependency.expected_sha256 == resource_content_hash(source.resources[0], source)
    directory = write_config_package(tmp_path / "config", selected)
    archive = seal_directory(directory, tmp_path / "brain.gaconfig")
    imported = read_resource_set(archive)
    assert imported.files == selected.files
    assert imported.resources[0].dependencies[0] == dependency
    assert resource_content_hash(imported.resources[0], imported) == resource_content_hash(source.resources[1], source)
    with pytest.raises(PackageError, match="dependencies missing"):
        validate_resource_set(imported)


@pytest.mark.parametrize("file_source", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
def test_builder_merges_duplicate_attachments_only_when_bytes_match(tmp_path, file_source, conflicting):
    resources = _skills()
    logical = "assets/shared-image.png"
    original = b"shared attachment bytes"
    resources.files[logical] = original
    definition = _definition()
    definition["world"]["assets"] = [{"logical_path": logical,
        "asset_hash": "sha256:" + hashlib.sha256(original).hexdigest(),
        "media_type": "image/png", "size": len(original)}]
    supplied = b"different attachment bytes" if conflicting else original
    source = tmp_path / "image.png" if file_source else supplied
    if file_source:
        source.write_bytes(supplied)
    destination = tmp_path / "experiment"

    def build():
        return ExperimentPackageBuilder().build_directory(destination,
            definition=definition, skills=[], brain_skill="daily-brain",
            resource_set=resources, asset_sources={logical: source})

    if conflicting:
        with pytest.raises(PackageError, match="conflicting resource attachment: assets/shared-image.png"):
            build()
        assert not destination.exists()
    else:
        build()
        validate_experiment_directory(destination)
        copied = read_resource_set(destination)
        assert copied.files[logical] == original
        assert logical in copied.get(ResourceRef(kind="map", key="shared-home")).attachments
    assert resources.files[logical] == original


def test_imported_dependency_rejects_same_key_with_different_script_bytes():
    source = _skills()
    selected = select_resources(source, [ResourceRef(kind="skill", key="daily-brain")])
    selected.files["skills/items/read-clock/scripts/read.py"] = b"print('different')\n"
    with pytest.raises(PackageError, match="content conflict"):
        validate_resource_set(selected)


def test_config_full_closure_preserves_binary_templates_and_deterministic_zip(tmp_path: Path):
    source = _skills()
    selected = select_resources(source, [ResourceRef(kind="skill", key="daily-brain")])
    fixed_time = datetime(2026, 9, 22, tzinfo=UTC)
    directory = write_config_package(tmp_path / "config", selected, created_at=fixed_time)
    first = seal_directory(directory, tmp_path / "one.gaconfig")
    second = seal_directory(directory, tmp_path / "two.gaconfig")
    assert first.read_bytes() == second.read_bytes()
    assert read_resource_set(first).files == source.files


def test_shared_content_survives_experiment_and_run_without_placement_in_agent_core(tmp_path: Path):
    experiment = _experiment(tmp_path)
    resources = read_resource_set(experiment)
    agent = resources.get(ResourceRef(kind="agent", key="alice"))
    assert "coord" not in agent.definition and "spatial" not in agent.definition
    manifest = read_json(experiment / "manifest.json")
    assert manifest["protocol"] == "ga-package" and manifest["schema_version"] == 2
    assert set(manifest["entrypoints"]) == {"resources", "assembly"}
    assert not (experiment / "agents/index.json").exists()
    assert not (experiment / "skills/registry.json").exists()
    before = {record.identity: resource_content_hash(record, resources) for record in resources.resources}
    run = RunService().create(experiment, tmp_path / "run", requested_steps=2)
    embedded = read_resource_set(run)
    assert {record.identity: resource_content_hash(record, embedded) for record in embedded.resources} == before
    loaded = load_experiment_directory(run / "experiment")
    assert loaded.definition.agents[0].coord == (0, 0)
    assert loaded.skill_snapshot["read-clock"]["files"]["templates/example.bin"] == b"\x00\xff\x03"
    from generative_agents.ga_protocol.skills.documents import SnapshotSkillRegistry
    runtime_skills = SnapshotSkillRegistry(loaded.skill_snapshot, root=tmp_path / "runtime-skills")
    child = runtime_skills.get("read-clock")
    assert (child.path.parent / "templates/example.bin").read_bytes() == b"\x00\xff\x03"
    assert _experiment_definition(experiment)[1]["crowds"][0]["description"] == "Shared team"


def test_goal_edit_preserves_unchanged_map_and_attachments(tmp_path: Path, monkeypatch):
    experiment = _experiment(tmp_path)
    before = read_resource_set(experiment)
    _, definition = _experiment_definition(experiment)
    definition["experiment"]["goal"] = "new metadata"
    def unexpected(*args):
        raise AssertionError("unchanged map must not rebuild its semantic index")
    monkeypatch.setattr("generative_agents.ga_protocol.packages.definition.build_semantic_index", unexpected)
    write_experiment_definition(experiment, definition)
    write_integrity_manifest(experiment)
    after = read_resource_set(experiment)
    assert before.files == after.files
    assert before.get(ResourceRef(kind="map", key="shared-home")) == after.get(ResourceRef(kind="map", key="shared-home"))


def test_draft_skill_edit_refreshes_parent_dependency_without_a_revision_lock(tmp_path: Path):
    experiment = _experiment(tmp_path)
    resources = read_resource_set(experiment)
    parent = resources.get(ResourceRef(kind="skill", key="daily-brain"))
    previous_hash = parent.dependencies[0].expected_sha256
    assert previous_hash
    resources.files["skills/items/read-clock/scripts/read.py"] = b"print('edited clock')\n"
    definition = _experiment_definition(experiment)[1]
    write_experiment_definition(experiment, definition, resource_set=resources)
    write_integrity_manifest(experiment)
    validate_experiment_directory(experiment)
    changed = read_resource_set(experiment)
    new_hash = changed.get(ResourceRef(kind="skill", key="daily-brain")).dependencies[0].expected_sha256
    assert new_hash != previous_hash
    assert new_hash == resource_content_hash(changed.get(ResourceRef(kind="skill", key="read-clock")), changed)


def test_agent_content_hash_ignores_transport_path_and_explicit_default_values():
    first = ResourceSet([ResourceRecord(kind="agent", key="alice", name="Alice",
        definition={"scratch": {"age": 30}, "portrait_asset": "assets/alice.png"}, attachments=["assets/alice.png"])],
        {"assets/alice.png": b"same image"})
    second = ResourceSet([ResourceRecord(kind="agent", key="alice", name="Alice",
        definition={"scratch": {"age": 30}, "portrait_asset": "assets/imported/renamed.png", "enabled": True},
        attachments=["assets/imported/renamed.png"])], {"assets/imported/renamed.png": b"same image"})
    assert resource_content_hash(first.resources[0], first) == resource_content_hash(second.resources[0], second)


def test_experiment_rejects_missing_agent_picture_even_after_integrity_is_rebuilt(tmp_path: Path):
    experiment = _experiment(tmp_path)
    index = read_json(experiment / "resources/index.json")
    agent = next(record for record in index["resources"] if record["kind"] == "agent")
    agent["definition"]["portrait_asset"] = "assets/missing.png"
    from generative_agents.ga_protocol.packages.io import atomic_write_json
    atomic_write_json(experiment / "resources/index.json", index)
    write_integrity_manifest(experiment)
    with pytest.raises(PackageError, match="undeclared attachment"):
        validate_experiment_directory(experiment)


def test_current_manifest_rejects_old_envelope_and_duplicate_brain_authority(tmp_path: Path):
    experiment = _experiment(tmp_path)
    manifest = read_json(experiment / "manifest.json")
    with pytest.raises(ValueError):
        ExperimentManifest.model_validate(dict(manifest, schema_version=1))
    assembly_path = experiment / "runtime/assembly.json"
    assembly = read_json(assembly_path)
    assembly["engine"]["brain_skill"] = "different-brain"
    from generative_agents.ga_protocol.packages.io import atomic_write_json
    atomic_write_json(assembly_path, assembly)
    write_integrity_manifest(experiment)
    with pytest.raises(PackageError, match="only to experiment assembly"):
        validate_experiment_directory(experiment)


@pytest.mark.parametrize("path", ["../escape.png", "C:/secret.txt", "images/../escape.png", "assets/file:stream"])
def test_resource_attachments_reject_unsafe_cross_platform_paths(path):
    with pytest.raises(ValueError):
        ResourceRecord(kind="agent", key="alice", attachments=[path])
