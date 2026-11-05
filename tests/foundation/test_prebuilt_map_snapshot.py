"""Authored 3D maps copy local metadata and a scene reference into portable experiments."""
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.packages.io import read_json, seal_directory
from generative_agents.ga_protocol.packages.resources import read_resource_set
from generative_agents.ga_protocol.packages.semantic_index import build_semantic_index
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_replay.api import run_scene_reference
from generative_agents.ga_runtime.api import RunService
from generative_agents.ga_studio.api import ExperimentSelection, ExperimentWorkspaceService, StudioSession, StudioAgentDefinition
from generative_agents.ga_studio.experiments.editor import ExperimentResourceEditor, ExperimentResourceError
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.storage.assets import AssetStore
from tests.studio_support import create_test_studio
from tests.test_portable_package_protocol import _definition


@pytest.fixture
def author(tmp_path):
    session = StudioSession("sqlite:///" + (tmp_path/"studio.db").as_posix(), tmp_path)
    brain = session.skills.create(name="scene-brain", description="test", kind="brain")
    agent = session.resources.create_agent(StudioAgentDefinition(agent_key="walker", name="Walker", scratch={"age":28}))
    model = session.resources.create_model_preset(name="test", config=_definition()["models"])
    wk = ExperimentWorkspaceService(session.database, package_root=tmp_path/"packages", var_dir=tmp_path,
                                   skill_registry=session.skills)
    yield session, wk, dict(brain_skill_id=brain.resource_id, model_preset_id=model["id"], agent_ids=(agent["id"],))
    session.close()


def test_authored_scene_selection_sealing_and_run_preserve_metadata_and_reference(author, tmp_path):
    session, wk, selected_resources = author
    public = wk.map_service.create_map(name="通勤场景", map_key="commute-scene", description="用于通勤行为实验",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    selected = wk.create(ExperimentSelection(name="Online map", goal="", map_id=public["id"], **selected_resources))
    root = Path(selected["location"])
    validate_experiment_directory(root)
    record = next(r for r in read_json(root/"resources/index.json")["resources"] if r["kind"] == "map")
    assert (record["key"], record["name"], record["description"]) == ("commute-scene", "通勤场景", "用于通勤行为实验")
    assert record["definition"]["definition"] == {"environment_type":"spatial_3d","simworld_scene_id":"map-15"}
    assert record["attachments"] == [] and not record["definition"].get("assets")
    assert wk.simworld_scene_id(selected["experiment_id"]) == "map-15"
    assert wk.simworld_scene_participants(selected["experiment_id"])[0]["agent_key"] == "walker"
    detail = ExperimentResourceEditor(wk).read(selected["experiment_id"], f"maps/{selected['experiment_id']}", {})
    assert (detail["map_key"], detail["name"], detail["description"]) == (record["key"], record["name"], record["description"])
    exchange = ResourceExchangeService(session.database, asset_store=AssetStore(tmp_path))
    config_zip = exchange.export_resource("map", public["id"], tmp_path/"scene.zip")
    wk.map_service.delete_map(public["id"])
    assert next(r for r in read_json(root/"resources/index.json")["resources"] if r["kind"] == "map") == record
    assert wk.simworld_scene_id(selected["experiment_id"]) == "map-15"
    archive = seal_directory(root, tmp_path/"scene.gaexp")
    run = RunService().create(archive, tmp_path/"run", requested_steps=2)
    ref = run_scene_reference(run, read_json(run/"run.json")["run_id"])
    assert ref["scene_id"] == "map-15" and ref["participants"] == {"walker": None}
    assert not any(f.suffix in {".pak",".glb",".jpg",".umap"} for f in run.rglob("*"))
    for package in (config_zip, archive, RunService().seal(run, tmp_path/"scene.garun")):
        portable = next(item for item in read_resource_set(package).resources if item.kind == "map")
        assert (portable.key, portable.name, portable.description) == (record["key"], record["name"], record["description"])
        assert portable.definition["definition"] == record["definition"]["definition"]
        assert not portable.attachments
    imported = exchange.import_resources(config_zip, [{"kind": "map", "key": public["map_key"]}])
    restored = wk.map_service.get_map(imported["imported"][0]["id"])
    assert restored["id"] != public["id"]
    assert (restored["map_key"], restored["name"], restored["description"], restored["world"]) == (
        public["map_key"], public["name"], public["description"], public["world"])


def test_map_replacement_uses_author_identity_and_copies_metadata_with_reset_placements(author):
    _session, wk, selected_resources = author
    first = wk.map_service.create_map(name="起始讨论", environment_type="social_1d", presentation="chat")
    scene_a = wk.map_service.create_map(name="早间出行", map_key="morning-trip", description="早间行程背景",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    scene_b = wk.map_service.create_map(name="晚间出行", map_key="evening-trip", description="晚间行程背景",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    assert scene_a["id"] != scene_b["id"]
    created = wk.create(ExperimentSelection(name="切换地图", goal="", map_id=first["id"], **selected_resources))
    identity = created["experiment_id"]
    editor = ExperimentResourceEditor(wk)
    detail = editor.read(identity, f"maps/{identity}", {})
    for selected in (scene_a, scene_b):
        detail = editor.write(identity, f"maps/{identity}/selection", "PUT", {
            "map_id": selected["id"], "expected_content_sha256": detail["row_version"]})
        assert (detail["map_key"], detail["name"], detail["description"]) == (
            selected["map_key"], selected["name"], selected["description"])
        assert detail["world"]["definition"] == {"environment_type": "spatial_3d", "simworld_scene_id": "map-15"}
        assert wk.simworld_scene_participants(identity) == [{"agent_key": "walker", "name": "Walker", "spawn_anchor": "auto", "yaw_deg": 0.0}]
    wk.map_service.delete_map(scene_b["id"])
    assert editor.read(identity, f"maps/{identity}", {}) == detail
    root = Path(created["location"])
    before = (root/"resources/index.json").read_bytes()
    for invalid in ({}, {"simworld_scene_id": "map-2"}, {"map_id": scene_a["id"], "simworld_scene_id": "map-2"}):
        with pytest.raises(ExperimentResourceError, match="公共地图"):
            editor.write(identity, f"maps/{identity}/selection", "PUT", {
                **invalid, "expected_content_sha256": detail["row_version"]})
        assert (root/"resources/index.json").read_bytes() == before


def _editable_public_map(workspace, kind):
    public = workspace.map_service.create_map(name="可编辑地图", environment_type=kind, width=2, height=1)
    if kind == "spatial_2d":
        world = public["world"]
        world["definition"]["tile_address_keys"] = ["world", "sector", "arena", "game_object"]
        for tile in world["definition"]["tiles"]:
            tile["address"] = [world["world_name"], "街区", "广场"]
        world["definition"]["tiles"][1]["address"].append("长椅")
        world, _ = build_semantic_index(world)
        public = workspace.map_service.update_map(public["id"], expected_lock_version=public["row_version"], world=world)
    return public


@pytest.mark.parametrize("current_kind,incoming_kind", [
    ("spatial_3d", "spatial_3d"), ("spatial_3d", "social_1d"), ("spatial_3d", "spatial_2d"),
    ("social_1d", "spatial_3d"), ("spatial_2d", "spatial_3d"),
])
def test_experiment_scene_changes_cannot_bypass_map_selection_via_save_or_validate(author, current_kind, incoming_kind):
    _session, workspace, selected_resources = author
    scene = workspace.map_service.create_map(name="已选择场景", map_key="selected-scene", description="应保留的用途说明",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    local_kind = current_kind if current_kind != "spatial_3d" else incoming_kind
    local = _editable_public_map(workspace, local_kind) if local_kind != "spatial_3d" else scene
    current = scene if current_kind == "spatial_3d" else local
    incoming = copy.deepcopy((scene if incoming_kind == "spatial_3d" else local)["world"])
    if incoming_kind == "spatial_3d":
        incoming["definition"]["simworld_scene_id"] = "unlisted-scene"
        incoming["world_name"] = "不应写入的名称"
    created = workspace.create(ExperimentSelection(name="地图选择保护", goal="", map_id=current["id"], **selected_resources))
    identity, root = created["experiment_id"], Path(created["location"])
    editor = ExperimentResourceEditor(workspace)
    before = editor.read(identity, f"maps/{identity}", {})
    package_files = {path: (root/path).read_bytes() for path in (
        "manifest.json", "resources/index.json", "runtime/assembly.json", "integrity/sha256.json")}
    for method, suffix in (("PUT", ""), ("PUT", "/validate"), ("POST", "/validate")):
        with pytest.raises(ExperimentResourceError, match="公共地图目录") as rejected:
            editor.write(identity, f"maps/{identity}{suffix}", method,
                {"row_version": before["row_version"], "world": incoming})
        assert rejected.value.status_code == 422
        assert editor.read(identity, f"maps/{identity}", {}) == before
        assert {path: (root/path).read_bytes() for path in package_files} == package_files


@pytest.mark.parametrize("kind", ["social_1d", "spatial_2d"])
def test_editable_experiment_maps_still_save_and_select_public_scenes(author, tmp_path, kind):
    _session, workspace, selected_resources = author
    public = _editable_public_map(workspace, kind)
    created = workspace.create(ExperimentSelection(name="保存与选图", goal="", map_id=public["id"], **selected_resources))
    identity = created["experiment_id"]
    editor = ExperimentResourceEditor(workspace)
    detail = editor.read(identity, f"maps/{identity}", {})
    changed = copy.deepcopy(detail["world"])
    if kind == "social_1d":
        changed["definition"]["description"] = "实验内的新用途说明"
    else:
        changed["definition"]["tiles"][1]["collision"] = True
    saved = editor.write(identity, f"maps/{identity}", "PUT", {"row_version": detail["row_version"], "world": changed})
    assert saved["row_version"] != detail["row_version"]
    assert saved["world"]["definition"] == changed["definition"]
    assert workspace.map_service.get_map(public["id"])["world"]["definition"] == public["world"]["definition"]
    scene = workspace.map_service.create_map(name="列表中的场景", map_key="listed-scene", description="场景用途",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    selected = editor.write(identity, f"maps/{identity}/selection", "PUT",
        {"row_version": saved["row_version"], "map_id": scene["id"]})
    assert (selected["name"], selected["map_key"], selected["description"]) == (scene["name"], scene["map_key"], scene["description"])
    with pytest.raises(ExperimentResourceError, match="公共地图目录"):
        editor.write(identity, f"maps/{identity}", "PUT", {"row_version": selected["row_version"], "world": selected["world"]})
    validated = editor.write(identity, f"maps/{identity}/validate", "POST",
        {"row_version": selected["row_version"], "world": selected["world"]})
    assert validated == selected
    base = f"/api/studio/experiments/{identity}/resources/maps/{identity}"
    app = create_test_studio(database_url="sqlite:///" + (tmp_path/"studio.db").as_posix(), var_dir=tmp_path)
    altered = copy.deepcopy(selected["world"])
    altered["definition"]["simworld_scene_id"] = "unlisted-scene"
    with TestClient(app) as client:
        rejected = client.put(base, json={"row_version": selected["row_version"], "world": altered})
        assert rejected.status_code == 422 and "公共地图目录" in rejected.json()["detail"]
        checked = client.post(base + "/validate", json={"world": altered})
        assert checked.status_code == 200 and checked.json()["validation"]["valid"]
    assert editor.read(identity, f"maps/{identity}", {}) == selected


@pytest.mark.parametrize("selection", [{}, {"map_id": None}, {"map_id": ""}, {"simworld_scene_id": "map-15"},
                                      {"map_id": "public-map", "simworld_scene_id": "map-15"}])
def test_experiment_http_requires_a_local_map_id_and_rejects_direct_scene_selection(tmp_path, selection):
    app = create_test_studio(database_url="sqlite:///" + (tmp_path/"studio.db").as_posix())
    with TestClient(app) as client:
        result = client.post("/api/studio/experiments", json={"name": "无公共地图", "brain_skill_id": "brain",
            "model_preset_id": "model", **selection})
        assert result.status_code == 422, result.text


@pytest.mark.parametrize("injected", [{"roads":[]},{"anchors":[]},{"prebuilt_scene":{"files":[]}},
                                       {"simworld_scene_id":"../map-15"}])
def test_simworld_definition_cannot_smuggle_assets_or_geometry(injected):
    with pytest.raises(ValidationError):
        WorldConfig(world_key="scene", world_name="scene", definition={
            "environment_type":"spatial_3d", "simworld_scene_id":"map-15", **injected})
