"""Social maps remain self-contained and never acquire spatial placeholders."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.packages.definition import _experiment_definition, read_experiment_assembly
from generative_agents.ga_protocol.packages.io import PackageError, read_json, seal_directory
from generative_agents.ga_protocol.packages.resources import read_resource_set, resource_content_hash
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_protocol.schemas.resources import SocialExperimentPlacement
from generative_agents.ga_studio.experiments.editor import ExperimentResourceEditor
from generative_agents.ga_studio.experiments.workspace import ExperimentSelection, ExperimentWorkspaceService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.maps import WorldMapService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.assets import AssetStore
from tests.studio_support import create_test_studio


@pytest.fixture
def social_studio(database, tmp_path):
    maps = WorldMapService(database)
    resources = StudioResourceService(database)
    skills = DatabaseSkillRegistry(database, cache_root=tmp_path / "skills")
    brain = skills.create(name="social-brain", description="讨论后决定发言或等待", kind="brain")
    agent = resources.create_agent({"agent_key": "alice", "name": "Alice", "scratch": {"age": 30, "daily_plan": "参与讨论"}})
    model = resources.create_model_preset(name="Test models", config={
        "chat": {"provider": "vllm", "model": "test", "base_url": "http://127.0.0.1:9999/v1"},
        "embedding": {"provider": "openai_compatible", "model": "test", "base_url": "http://127.0.0.1:9999/v1"},
    })
    workspace = ExperimentWorkspaceService(database, package_root=tmp_path / "packages", var_dir=tmp_path, skill_registry=skills)
    return maps, workspace, brain.resource_id, agent["id"], model["id"]


@pytest.mark.parametrize("presentation", ["chat", "forum"])
def test_social_map_selection_package_edit_and_exchange_are_coordinate_free(social_studio, database, tmp_path, presentation):
    maps, workspace, brain, agent, model = social_studio
    public = maps.create_map(name="讨论空间", map_key="discussion-room", environment_type="social_1d",
                             presentation=presentation, description="公开交流", rules="有依据地讨论")
    assert public["environment_type"] == "social_1d"
    assert public["presentation"] == presentation
    assert public["dimensions"] is None
    listed = maps.list_maps()["items"][0]
    assert listed["environment_type"] == "social_1d" and listed["presentation"] == presentation
    checked = maps.validate_map(public["id"], expected_lock_version=public["lock_version"])
    assert checked["validation"]["valid"]
    assert [item["code"] for item in checked["validation"]["checks"]] == ["SOCIAL_SPACE_CONTRACT"]
    cloned = maps.create_map(name="独立副本", map_key="copied-room", source_map_id=public["id"])
    assert cloned["world"]["world_key"] == "copied-room" and cloned["world"]["world_name"] == "独立副本"
    assert cloned["world"]["definition"] == public["world"]["definition"]
    created = workspace.create(ExperimentSelection(name="社交实验", goal="讨论", map_id=public["id"],
        brain_skill_id=brain, model_preset_id=model, agent_ids=(agent,)))
    root = Path(created["location"])
    definition = _experiment_definition(root)[1]
    copied = definition["world"]["definition"]
    assert copied == {"environment_type": "social_1d", "presentation": presentation, "description": "公开交流", "rules": "有依据地讨论"}
    assert definition["agents"][0]["space_id"] == "discussion-room"
    assert not {"coord", "spatial"} & definition["agents"][0].keys()
    assembly = read_experiment_assembly(root)
    assert isinstance(assembly.placements[0], SocialExperimentPlacement)
    assert set(read_json(root / "runtime/assembly.json")["placements"][0]) == {"agent", "space_id"}
    editor = ExperimentResourceEditor(workspace)
    identity = created["experiment_id"]
    assert editor.read(identity, "maps", {})["items"][0]["presentation"] == presentation
    assert "coord" not in editor.read(identity, "agents", {})["items"][0]
    assert editor.read(identity, "agents/alice", {})["definition"]["space_id"] == "discussion-room"
    changed = copy.deepcopy(definition["world"])
    changed["definition"]["rules"] = "允许保持沉默"
    editor.write(identity, f"maps/{identity}", "PUT", {"row_version": created["content_sha256"], "world": changed})
    assert _experiment_definition(root)[1]["world"]["definition"]["rules"] == "允许保持沉默"
    assert maps.get_map(public["id"])["world"]["definition"]["rules"] == "有依据地讨论"
    validate_experiment_directory(root)
    archive = seal_directory(root, tmp_path / "social.gaexp")
    packaged = read_resource_set(archive)
    assert next(record for record in packaged.resources if record.kind == "map").definition["definition"]["presentation"] == presentation
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path))
    public_zip = exchange.export_resource("map", public["id"], tmp_path / "social-map.zip")
    resources = read_resource_set(public_zip)
    record = resources.resources[0]
    before = copy.deepcopy(record.definition)
    resource_content_hash(record, resources)
    assert before == record.definition and "size_unit" not in record.definition["definition"]
    updated = copy.deepcopy(public["world"])
    updated["world_name"] = "修改后的公共空间"
    updated["definition"]["description"] = "公共资源的新说明"
    saved = maps.update_map(public["id"], expected_lock_version=public["lock_version"], world=updated)
    assert saved["name"] == "修改后的公共空间" and saved["description"] == "公共资源的新说明"
    assert _experiment_definition(root)[1]["world"]["world_name"] == "讨论空间"
    maps.delete_map(public["id"])
    validate_experiment_directory(root)
    preview = exchange.preview_import(public_zip)
    assert preview["can_import"]
    imported = exchange.import_resources(public_zip, [{"kind": "map", "key": "discussion-room"}])
    restored = maps.get_map(imported["imported"][0]["id"])
    assert restored["id"] != public["id"]
    assert restored["world"]["definition"] == copied


@pytest.mark.parametrize("extra", [{"tiles": []}, {"editor": {}}, {"environment_type": "spatial_3d"}, {"presentation": "mixed"}])
def test_social_map_rejects_geometry_unknown_types_and_mixed_presentation(extra):
    with pytest.raises(ValidationError):
        WorldConfig(world_key="discussion-room", world_name="Room", definition={"environment_type": "social_1d", "presentation": "chat", **extra})


def test_social_membership_rejects_coordinates_and_foreign_space_atomically(social_studio):
    maps, workspace, brain, agent, model = social_studio
    public = maps.create_map(name="Room", map_key="discussion-room", environment_type="social_1d", presentation="chat")
    created = workspace.create(ExperimentSelection(name="Social", goal="", map_id=public["id"],
        brain_skill_id=brain, model_preset_id=model, agent_ids=(agent,)))
    root = Path(created["location"])
    original = read_json(root / "integrity/sha256.json")["root_sha256"]
    for field, value in [("space_id", "foreign-room"), ("coord", [0, 0])]:
        definition = _experiment_definition(root)[1]
        definition["agents"][0][field] = value
        with pytest.raises((PackageError, ValidationError)):
            workspace.replace_definition(created["experiment_id"], definition)
        assert read_json(root / "integrity/sha256.json")["root_sha256"] == original
        validate_experiment_directory(root)


@pytest.mark.parametrize("presentation", ["chat", "forum"])
def test_social_map_http_contract_does_not_require_spatial_fields(database_url, presentation):
    with TestClient(create_test_studio(database_url=database_url)) as client:
        response = client.post("/api/studio/resources/maps", json={"name": "公开讨论", "map_key": "public-discussion",
            "environment_type": "social_1d", "presentation": presentation, "rules": "可以选择沉默"})
        assert response.status_code == 201, response.text
        detail = response.json()
        assert detail["environment_type"] == "social_1d" and detail["presentation"] == presentation
        validated = client.post(f"/api/studio/resources/maps/{detail['id']}/validate", params={"row_version": detail["row_version"]})
        assert validated.status_code == 200 and validated.json()["validation"]["valid"]
        world = detail["world"]
        world["definition"]["rules"] = "引用真实消息后回复"
        saved = client.put(f"/api/studio/resources/maps/{detail['id']}", json={"row_version": detail["row_version"], "world": world})
        assert saved.status_code == 200, saved.text
        assert saved.json()["world"]["definition"]["rules"] == "引用真实消息后回复"
        rejected = client.post("/api/studio/resources/maps", json={"name": "尚未实现", "environment_type": "spatial_3d"})
        assert rejected.status_code == 422
