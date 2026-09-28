"""Cross-machine public resource exchange, including atomic failed imports."""
from __future__ import annotations

import copy
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import select, func

from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceRef, ResourceSet, read_resource_set, write_config_package,
)
from generative_agents.ga_studio.resources.assets import AssetService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.exchange import (
    ResourceExchangeService, ResourceExchangeError, require_resolved_resource_dependencies,
)
from generative_agents.ga_studio.resources.maps import WorldMapService, normalize_public_world
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.assets import AssetStore
from generative_agents.ga_studio.storage.database import create_database, upgrade_database
from generative_agents.ga_studio.storage.models import (
    Asset, Base, StudioAgent, StudioCrowd, StudioSkill, StudioModelPreset,
    SpatialAssetDefinition, StudioResourceExchangeState, WorldMap,
)
from generative_agents.ga_studio.storage.schema import prepare_studio_database


def png(width=128, height=128, color="red"):
    stream = BytesIO()
    Image.new("RGBA", (width, height), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def machines(tmp_path):
    opened = []
    def machine(name):
        root = tmp_path / name
        root.mkdir()
        database = create_database(f"sqlite:///{(root / 'studio.db').as_posix()}")
        Base.metadata.create_all(database.engine)
        opened.append(database)
        store = AssetStore(root)
        return database, ResourceExchangeService(database, asset_store=store), store
    yield machine
    for database in opened:
        database.close()


def agent(database, store, key="alice", *, images=True):
    uploaded = AssetService(database, var_dir=store.root.parent).upload_database_images({
        "portrait": (BytesIO(png(64, 64)), "portrait.png"),
        "sprite": (BytesIO(png()), "sprite.png"),
    }) if images else {}
    definition = {"agent_key": key, "name": key.title(), "scratch": {"age": 29},
                  "perception": {"vision_radius": 6, "attention_bandwidth": 5},
                  "sprite_display_tiles": 1.7, "tags": ["shared"],
                  "portrait_asset_id": uploaded.get("portrait", {}).get("asset_id"),
                  "sprite_asset_id": uploaded.get("sprite", {}).get("asset_id")}
    return StudioResourceService(database).create_agent(definition, description="角色说明")


def package(tmp_path, resources, name="incoming"):
    return write_config_package(tmp_path / name, resources)


def import_all(service, path):
    preview = service.preview_import(path)
    refs = [{"kind": item["kind"], "key": item["key"]} for item in preview["resources"]]
    return service.import_resources(path, refs)


def test_agent_images_and_public_fields_roundtrip_without_host_ids(machines, tmp_path):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    original = agent(db1, store)
    archive = source.export_resource("agent", original["id"], tmp_path / "agent.zip")
    contents = read_resource_set(archive)
    assert len(contents.files) == 2
    record = contents.resources[0]
    assert "portrait_asset_id" not in record.definition
    assert "coord" not in record.definition and "spatial" not in record.definition
    assert original["id"].encode() not in str(record).encode()
    preview = target.preview_import(archive)
    assert preview["source_kind"] == "config" and preview["can_import"]
    result = import_all(target, archive)
    imported = StudioResourceService(db2).get_agent(result["imported"][0]["id"])
    assert imported["id"] != original["id"]
    assert imported["definition"]["sprite_display_tiles"] == 1.7
    with db2.session_factory() as session:
        for role, expected in (("portrait", png(64, 64)), ("sprite", png())):
            row = session.get(Asset, imported["definition"][f"{role}_asset_id"])
            assert row.content_blob == expected
    assert target.preview_import(archive)["resources"][0]["status"] == "reuse"
    assert import_all(target, archive)["imported"] == []
    reexport = target.export_resource("agent", imported["id"], tmp_path / "agent-return.zip")
    assert source.preview_import(reexport)["resources"][0]["status"] == "reuse"


def test_crowd_can_import_alone_preserves_missing_members_and_resolves_exact_later(machines, tmp_path):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    member = agent(db1, store)
    crowd = StudioResourceService(db1).create_crowd(name="同学", crowd_key="classmates", agent_ids=[member["id"]])
    # A sparse v2 config is still accepted; normal Crowd export is now complete.
    archive = package(tmp_path, source.resource_set("crowd", crowd["id"]), "sparse-crowd")
    result = import_all(target, archive)
    crowd_id = result["imported"][0]["id"]
    local = StudioResourceService(db2).get_crowd(crowd_id)
    assert local["agent_ids"] == []
    assert local["pending_dependencies"][0]["key"] == "alice"
    with db2.session_factory() as session, pytest.raises(ResourceExchangeError, match="尚未绑定"):
        require_resolved_resource_dependencies(session, "crowd", crowd_id)
    reexport = target.resource_set("crowd", crowd_id)
    assert reexport.resources[0].definition["members"][0]["key"] == "alice"
    assert reexport.resources[0].dependencies[0].expected_sha256
    agent_archive = source.export_resource("agent", member["id"], tmp_path / "member.zip")
    import_all(target, agent_archive)
    local = StudioResourceService(db2).get_crowd(crowd_id)
    assert len(local["agent_ids"]) == 1 and local["pending_dependencies"] == []


def test_unselected_dependency_never_binds_existing_same_key_without_explicit_choice(machines, tmp_path):
    database, exchange, store = machines("target")
    local_agent = agent(database, store, images=False)
    reference = ResourceRef(kind="agent", key="alice")
    crowd = ResourceRecord(kind="crowd", key="unbound-class", name="待绑定人群",
        definition={"members": [reference.model_dump(mode="json", exclude_none=True)]}, dependencies=[reference])
    archive = package(tmp_path, ResourceSet(resources=[crowd]), "unbound")
    crowd_id = import_all(exchange, archive)["imported"][0]["id"]
    service = StudioResourceService(database)
    pending = service.get_crowd(crowd_id)
    assert pending["agent_ids"] == [] and pending["pending_dependencies"][0]["key"] == "alice"
    own_agent = exchange.export_resource("agent", local_agent["id"], tmp_path / "existing-agent.zip")
    import_all(exchange, own_agent)
    with database.session_factory() as session, pytest.raises(ResourceExchangeError, match="尚未绑定"):
        require_resolved_resource_dependencies(session, "crowd", crowd_id)
    # Without a source content hash, even separately importing the same key is
    # insufficient. An explicit author member choice confirms the local binding.
    saved = service.save_crowd(crowd_id, agent_ids=[local_agent["id"]], expected_row_version=pending["row_version"])
    assert saved["agent_ids"] == [local_agent["id"]] and saved["pending_dependencies"] == []
    with database.session_factory() as session:
        require_resolved_resource_dependencies(session, "crowd", crowd_id)


def test_skill_brain_and_private_templates_roundtrip(machines, tmp_path):
    db1, source, _ = machines("source")
    db2, target, _ = machines("target")
    registry = DatabaseSkillRegistry(db1, cache_root=tmp_path / "source-cache")
    child = registry.create(name="observe-room", description="观察空间")
    brain = registry.create(name="daily-brain", description="日常大脑", kind="brain")
    brain_markdown = brain.markdown + "\n调用 $observe-room。\n"
    registry.save("daily-brain", brain_markdown)
    export = source.export_resource("brain", "daily-brain", tmp_path / "brain.zip")
    refs = read_resource_set(export)
    assert {item.key for item in refs.resources} == {"daily-brain", "observe-room"}
    assert any(item.definition.get("skill_kind") == "brain" for item in refs.resources)
    import_all(target, export)
    assert target.catalog("brain")["items"][0]["skill_kind"] == "brain"
    markdown = b'\xef\xbb\xbf---\r\nname: template-skill\r\ndescription: "Template skill"\r\n---\r\nUse templates/note.txt.\r\n'
    files = {"skills/items/template-skill/SKILL.md": markdown,
             "skills/items/template-skill/templates/note.txt": "精确模板\n".encode(),
             "skills/items/template-skill/scripts/note.py": b'\xef\xbb\xbfprint("exact-script")\r\n'}
    template = ResourceRecord(kind="skill", key="template-skill", name="template-skill", description="Template skill",
                              definition={"skill_kind": "atomic", "entrypoint": "skills/items/template-skill/SKILL.md"},
                              attachments=list(files))
    path = package(tmp_path, ResourceSet(resources=[template], files=files), "templates")
    import_all(target, path)
    target_registry = DatabaseSkillRegistry(db2, cache_root=tmp_path / "target-cache")
    target_registry.save("template-skill", markdown.decode())
    emitted = target.resource_set("skill", "template-skill")
    assert emitted.files == files
    assert target.preview_import(path)["resources"][0]["status"] == "reuse"
    assert all(item["status"] == "reuse" for item in target.preview_import(export)["resources"])
    reexport = target.export_resource("brain", "daily-brain", tmp_path / "brain-return.zip", include_dependencies=True)
    assert all(item["status"] == "reuse" for item in source.preview_import(reexport)["resources"])


def test_crowd_export_and_root_import_include_members_and_images_by_default(machines, tmp_path):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    members = [agent(db1, store, key) for key in ("alice", "bob")]
    crowd = StudioResourceService(db1).create_crowd(name="同学", crowd_key="classmates", agent_ids=[item["id"] for item in members])
    archive = source.export_resource("crowd", crowd["id"], tmp_path / "crowd.zip", include_dependencies=False)
    contents = read_resource_set(archive)
    assert {item.identity for item in contents.resources} == {("crowd", "classmates"), ("agent", "alice"), ("agent", "bob")}
    assert len(contents.files) == 2  # Identical images shared by both members.
    preview = target.preview_import(archive)
    assert preview["roots"] == [{"kind": "crowd", "key": "classmates"}]
    exported = source.preview_export("crowd", crowd["id"])
    assert len(exported["resources"]) == 3
    result = target.import_resources(archive, [{"kind": "crowd", "key": "classmates"}])
    assert len(result["imported"]) == 3 and result["pending_dependencies"] == []
    imported = next(item for item in result["imported"] if item["kind"] == "crowd")
    local_ids = StudioResourceService(db2).get_crowd(imported["id"])["agent_ids"]
    assert len(local_ids) == 2 and set(local_ids).isdisjoint(item["id"] for item in members)
    repeat = target.import_resources(archive, [{"kind": "crowd", "key": "classmates"}])
    assert not repeat["imported"] and len(repeat["reused"]) == 3
    _, extracting, _ = machines("extracting")
    single = extracting.import_resources(archive, [{"kind": "agent", "key": "bob"}])
    assert [item["key"] for item in single["imported"]] == ["bob"]


def test_brain_and_pack_exports_collect_nested_shared_skills(machines, tmp_path):
    database, source, _ = machines("source")
    _, target, _ = machines("target")
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "skills")
    registry.create(name="observe", description="观察")
    for key in ("morning", "evening"):
        pack = registry.create(name=key, description=key, kind="pack")
        registry.save(key, pack.markdown + "\n调用 $observe。\n")
    brain = registry.create(name="daily", description="日常", kind="brain")
    registry.save("daily", brain.markdown + "\n调用 $morning 和 $evening。\n")
    archive = source.export_resource("brain", "daily", tmp_path / "brain.zip")
    assert sorted(item.key for item in read_resource_set(archive).resources) == ["daily", "evening", "morning", "observe"]
    single = target.import_resources(archive, [{"kind": "skill", "key": "morning"}])
    assert {item["key"] for item in single["imported"]} == {"morning", "observe"}
    pack = source.export_resource("skill", "morning", tmp_path / "pack.zip")
    assert {item.key for item in read_resource_set(pack).resources} == {"morning", "observe"}
    alone = source.export_resource("skill", "observe", tmp_path / "alone.zip")
    assert [item.key for item in read_resource_set(alone).resources] == ["observe"]


def test_complete_exports_reject_missing_archived_and_unbound_dependencies(machines, tmp_path):
    database, source, store = machines("source")
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "skills")
    brain = registry.create(name="daily", description="日常", kind="brain")
    registry.save("daily", brain.markdown + "\n调用 $missing。\n")
    with pytest.raises(ResourceExchangeError, match="daily.*missing"):
        source.preview_export("brain", "daily")
    member = agent(database, store)
    service = StudioResourceService(database)
    crowd = service.create_crowd(name="同学", crowd_key="classmates", agent_ids=[member["id"]])
    sparse = package(tmp_path, source.resource_set("crowd", crowd["id"]), "sparse")
    service.archive("agent", member["id"])
    with pytest.raises(ResourceExchangeError, match="classmates.*alice"):
        source.export_resource("crowd", crowd["id"], tmp_path / "archived.zip")
    _, target, _ = machines("target")
    result = import_all(target, sparse)
    with pytest.raises(ResourceExchangeError, match="尚未绑定.*classmates.*alice"):
        target.export_resource("crowd", result["imported"][0]["id"], tmp_path / "unbound.zip")
    assert not (tmp_path / "archived.zip").exists() and not (tmp_path / "unbound.zip").exists()


def test_auto_included_member_conflict_rolls_back_the_entire_crowd(machines, tmp_path):
    db1, source, store1 = machines("source")
    db2, target, store2 = machines("target")
    member = agent(db1, store1)
    agent(db2, store2, images=False)
    crowd = StudioResourceService(db1).create_crowd(name="同学", crowd_key="classmates", agent_ids=[member["id"]])
    archive = source.export_resource("crowd", crowd["id"], tmp_path / "conflict.zip")
    with pytest.raises(ResourceExchangeError, match="整次导入已取消"):
        target.import_resources(archive, [{"kind": "crowd", "key": "classmates"}])
    with db2.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StudioCrowd)) == 0


def test_map_geometry_state_images_spatial_assets_remap_and_pending_skill(machines, tmp_path):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    registry = DatabaseSkillRegistry(db1, cache_root=tmp_path / "skills")
    registry.create(name="door-control", description="开关门")
    raw = png(64, 64, "blue")
    blob = store.put_stream(BytesIO(raw), logical_name="door.png", declared_media_type="image/png")
    with db1.session_factory.begin() as session:
        asset = Asset(sha256=blob.sha256, logical_name="door.png", media_type="image/png",
                      size_bytes=len(raw), relative_path=blob.relative_path)
        session.add(asset)
        contract = {"schema_version": "ga-spatial-asset/v2", "name": "门", "kind": "OBJECT",
                    "appearance": {"mode": "IMAGE", "asset_path": blob.relative_path,
                                   "state_variants": {"open": {"asset_path": blob.relative_path}}},
                    "skill_bindings": [{"interaction_key": "open-door", "skill_name": "door-control"}],
                    "initial_state": {"state": "closed"}}
        spatial = SpatialAssetDefinition(asset_key="door", name="门", description="", asset_kind="OBJECT",
                                         contract_json=contract, contract_hash="a" * 64)
        session.add(spatial)
        session.flush()
        spatial_id = spatial.id
        world = {"world_key": "shared-house", "world_name": "住宅", "assets": [],
                 "definition": {"size": [4, 4], "tile_size": 32, "size_unit": "TILE", "world": "住宅",
                                "tiles": [], "spatial_scene": {"schema_version": "ga-spatial-scene/v2",
                                "palette_refs": {}, "placements": [{"instance_key": "front-door", "spatial_asset_id": spatial.id,
                                "x_tiles": 1, "y_tiles": 2, "state_overrides": {"state": "closed"}}]}}}
        row = WorldMap(map_key="shared-house", name="住宅", description="几何不可丢", world_json=world, world_hash="b" * 64)
        session.add(row)
        session.flush()
        map_id = row.id
    archive = source.export_resource("map", map_id, tmp_path / "map.zip")
    resources = read_resource_set(archive)
    assert len(resources.resources) == 1 and len(resources.files) == 1
    assert spatial_id not in str(resources.resources[0].definition)
    imported = import_all(target, archive)
    local_id = imported["imported"][0]["id"]
    with db2.session_factory() as session:
        local = session.get(WorldMap, local_id)
        placement = local.world_json["definition"]["spatial_scene"]["placements"][0]
        assert placement["spatial_asset_id"] != spatial_id
        local_spatial = session.get(SpatialAssetDefinition, placement["spatial_asset_id"])
        assert local_spatial.contract_json["initial_state"] == {"state": "closed"}
        assert local_spatial.contract_json["appearance"]["asset_path"].startswith("assets/")
        with pytest.raises(ResourceExchangeError, match="尚未绑定"):
            require_resolved_resource_dependencies(session, "map", local_id)
    assert target.preview_import(archive)["resources"][0]["status"] == "reuse"
    reexport = target.export_resource("map", local_id, tmp_path / "map-return.zip")
    assert source.preview_import(reexport)["resources"][0]["status"] == "reuse"


def test_map_editor_images_slices_geometry_collision_and_state_roundtrip(machines, tmp_path):
    from tests.foundation.test_navigation import navigation_world
    from tests.test_portable_package_protocol import _definition
    from generative_agents.ga_studio.experiments.workspace import ExperimentSelection, ExperimentWorkspaceService
    from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
    db1, source, store = machines("source")
    db2, target, target_store = machines("target")
    raw = png(64, 32, "orange")
    digest = hashlib.sha256(raw).hexdigest()
    with db1.session_factory.begin() as session:
        image = Asset(sha256=digest, logical_name="state-strip.png", media_type="image/png",
                      size_bytes=len(raw), relative_path="", content_blob=raw)
        session.add(image)
        session.flush()
        original_asset_id = image.id
        world = navigation_world()
        editor = world["definition"]["editor_v2"]
        editor["material_sources"] = [{"id": "states", "name": "状态图", "kind": "UPLOADED",
            "asset_id": image.id, "asset_hash": digest, "media_type": "image/png", "width_px": 64,
            "height_px": 32, "tile_width": 32, "tile_height": 32, "columns": 2, "rows": 1, "tile_count": 2}]
        editor["material_slices"] = [{"id": key, "source_id": "states", "name": key, "kind": "TILE",
            "grid_rect": {"x": index, "y": 0, "width": 1, "height": 1},
            "pixel_rect": {"x": index * 32, "y": 0, "width": 32, "height": 32},
            "collision_cells": {"0": True}} for index, key in enumerate(("closed", "open"))]
        editor["hierarchy_nodes"][-1].update(
            bounds={"x": 1, "y": 1, "width": 1, "height": 1}, material_slice_id="closed",
            initial_state={"state": "closed"}, state_appearance={"cases": [{"value": "open", "material_slice_id": "open"}]})
        editor["navigation"] = {"base_blocked": [3, 10], "overrides": {"10": False}}
        normalized = normalize_public_world(world).model_dump(mode="json")
        row = WorldMap(map_key=world["world_key"], name=world["world_name"], description="编辑素材",
                       world_json=normalized, world_hash="a" * 64)
        session.add(row)
        session.flush()
        map_id = row.id
    archive = source.export_resource("map", map_id, tmp_path / "editable-map.zip")
    record = read_resource_set(archive).resources[0]
    assert original_asset_id not in str(record.definition)
    imported = import_all(target, archive)["imported"][0]
    loaded = WorldMapService(db2).get_map(imported["id"])["world"]
    actual_editor = loaded["definition"]["editor_v2"]
    original_editor = normalized["definition"]["editor_v2"]
    for field in ("material_slices", "hierarchy_nodes", "navigation"):
        assert actual_editor[field] == original_editor[field]
    local_asset_id = actual_editor["material_sources"][0]["asset_id"]
    assert local_asset_id != original_asset_id
    with db2.session_factory() as session:
        assert session.get(Asset, local_asset_id).content_blob == raw
    reexport = target.export_resource("map", imported["id"], tmp_path / "editable-map-return.zip")
    assert source.preview_import(reexport)["resources"][0]["status"] == "reuse"
    assert target.preview_import(reexport)["resources"][0]["status"] == "reuse"

    # Imported map attachments are collected both by ResourceSet and by the
    # experiment's materialized world. They must remain one verified copy.
    registry = DatabaseSkillRegistry(db2, cache_root=tmp_path / "target-skills")
    brain = registry.create(name="image-map-brain", description="image map test", kind="brain")
    person = agent(db2, target_store)
    model = StudioResourceService(db2).create_model_preset(
        name="模型", preset_key="test-model", config=_definition()["models"])
    workspace = ExperimentWorkspaceService(db2, package_root=tmp_path / "packages",
        var_dir=target_store.root.parent, skill_registry=registry)
    created = workspace.create(ExperimentSelection(name="导入图片地图实验", goal="",
        map_id=imported["id"], brain_skill_id=brain.resource_id,
        model_preset_id=model["id"], agent_ids=(person["id"],)))
    experiment = Path(created["location"])
    validate_experiment_directory(experiment)
    copied = read_resource_set(experiment)
    map_record = copied.get(ResourceRef(kind="map", key=world["world_key"]))
    assert len(map_record.attachments) == 1
    assert copied.files[map_record.attachments[0]] == raw
    agent_record = copied.get(ResourceRef(kind="agent", key="alice"))
    assert {copied.files[path] for path in agent_record.attachments} == {png(64, 64), png()}


def test_model_export_contains_only_declared_environment_not_credentials(machines, tmp_path):
    db1, source, _ = machines("source")
    db2, target, _ = machines("target")
    model = StudioResourceService(db1).create_model_preset(name="测试模型", preset_key="test-model", config={
        "chat": {"provider": "vllm", "base_url": "http://localhost:8080/v1", "model": "model-one",
                 "credential_env": "GA_MODEL_PRIVATE_ALIAS", "api_key": "NEVER-EXPORT-ME"}})
    archive = source.export_resource("model", model["id"], tmp_path / "model.zip")
    record = read_resource_set(archive).resources[0]
    assert "NEVER-EXPORT-ME" not in str(record)
    assert record.definition["chat"]["credential_env"] == "GA_MODEL_PRIVATE_ALIAS"
    imported = import_all(target, archive)
    config = StudioResourceService(db2).get_model_preset(imported["imported"][0]["id"])["config"]
    assert config["chat"]["model"] == "model-one" and "api_key" not in config["chat"]
    assert target.preview_import(archive)["resources"][0]["status"] == "reuse"
    reexport = target.export_resource("model", imported["imported"][0]["id"], tmp_path / "model-return.zip")
    assert source.preview_import(reexport)["resources"][0]["status"] == "reuse"


def test_conflicting_skill_does_not_partially_import_other_resources(machines, tmp_path):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    one = DatabaseSkillRegistry(db1, cache_root=tmp_path / "one")
    two = DatabaseSkillRegistry(db2, cache_root=tmp_path / "two")
    one.create(name="same-key", description="来源行为")
    two.create(name="same-key", description="不同的本机行为")
    person = agent(db1, store)
    resources = source.materialize_resources([("skill", "same-key"), ("agent", person["id"])])
    path = package(tmp_path, resources, "conflict")
    preview = target.preview_import(path)
    assert not preview["can_import"]
    with pytest.raises(ResourceExchangeError) as error:
        import_all(target, path)
    assert error.value.status_code == 409
    with db2.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StudioAgent)) == 0
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_late_import_failure_rolls_back_rows_and_image_blobs(machines, tmp_path, monkeypatch):
    db1, source, store = machines("source")
    db2, target, _ = machines("target")
    person = agent(db1, store)
    path = source.export_resource("agent", person["id"], tmp_path / "atomic.zip")
    original = target._insert
    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected late failure")
    monkeypatch.setattr(target, "_insert", failing)
    with pytest.raises(RuntimeError, match="injected"):
        import_all(target, path)
    with db2.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StudioAgent)) == 0
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_schema_addition_preserves_existing_public_database(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    upgrade_database(database_url)
    database = create_database(database_url)
    StudioResourceService(database).create_agent({"agent_key": "keep-me", "name": "保留", "scratch": {"age": 20}})
    StudioResourceExchangeState.__table__.drop(database.engine)
    database.close()
    result = prepare_studio_database(database_url, backup_dir=tmp_path / "must-not-create")
    assert not result.rebuilt and not (tmp_path / "must-not-create").exists()
    reopened = create_database(database_url)
    assert StudioResourceService(reopened).list_agents()[0]["agent_key"] == "keep-me"
    reopened.close()


def test_experiment_and_run_resources_reuse_original_public_resources(machines, tmp_path):
    from generative_agents.ga_studio.experiments.workspace import ExperimentSelection, ExperimentWorkspaceService
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from tests.test_portable_package_protocol import _definition
    database, exchange, store = machines("source")
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "skill-cache")
    brain = registry.create(name="test-brain", description="brain description", kind="brain")
    person = agent(database, store)
    crowd = StudioResourceService(database).create_crowd(name="班级", crowd_key="test-class", description="人群说明", agent_ids=[person["id"]])
    definition = _definition()
    world = definition["world"]
    world.pop("map_id")
    world.pop("map_snapshot_hash")
    with database.session_factory.begin() as session:
        row = WorldMap(map_key=world["world_key"], name=world["world_name"], description="地图说明",
                       world_json=world, world_hash="a" * 64)
        session.add(row)
        session.flush()
        map_id = row.id
    model = StudioResourceService(database).create_model_preset(name="完整模型", preset_key="combined-model",
                 description="模型说明", config=definition["models"])
    workspace = ExperimentWorkspaceService(database, package_root=tmp_path / "packages",
                                           var_dir=store.root.parent, skill_registry=registry)
    created = workspace.create(ExperimentSelection(name="往返实验", goal="test", map_id=map_id,
        brain_skill_id=brain.resource_id, model_preset_id=model["id"], crowd_ids=(crowd["id"],)))
    experiment = Path(created["location"])
    preview = exchange.preview_import(experiment)
    assert preview["source_kind"] == "exp"
    assert {item["kind"] for item in preview["resources"]} == {"map", "agent", "crowd", "skill", "model"}
    assert {f"{item['kind']}/{item['key']}": item["status"] for item in preview["resources"]} == {
        "map/test-world": "reuse", "agent/alice": "reuse", "crowd/test-class": "reuse",
        "skill/test-brain": "reuse", "model/combined-model": "reuse"}
    run = RunService().create(experiment, tmp_path / "run")
    archive = RunService().seal(run, tmp_path / "result.garun")
    run_preview = exchange.preview_import(archive)
    assert run_preview["source_kind"] == "run"
    assert all(item["status"] == "reuse" for item in run_preview["resources"])


@pytest.mark.parametrize("transport", ["config", "exp"])
def test_imported_map_with_implicit_tile_units_reuses_after_service_reopen(machines, tmp_path, transport):
    from tests.test_portable_package_protocol import _experiment
    from generative_agents.ga_protocol.packages.definition import _experiment_definition, write_experiment_definition
    from generative_agents.ga_protocol.packages.io import write_integrity_manifest
    database, exchange, store = machines("target")
    experiment = _experiment(tmp_path / "source")
    definition = _experiment_definition(experiment)[1]
    definition["world"]["definition"].pop("size_unit", None)
    definition["world"].pop("assets", None)
    write_experiment_definition(experiment, definition)
    write_integrity_manifest(experiment)
    resources = read_resource_set(experiment)
    world = next(record for record in resources.resources if record.kind == "map")
    assert "size_unit" not in world.definition["definition"]
    assert "assets" not in world.definition
    path = experiment
    if transport == "config":
        path = package(tmp_path, ResourceSet(resources=[world], files={name: resources.files[name] for name in world.attachments}), "map-config")
    result = exchange.import_resources(path, [{"kind": "map", "key": world.key}])
    with database.session_factory() as session:
        imported = session.get(WorldMap, result["imported"][0]["id"])
        assert imported.world_json["definition"]["size_unit"] == "TILE"
    reopened = ResourceExchangeService(database, asset_store=store)
    before = reopened.resource_set("map", world.key)
    preview = reopened.preview_import(path, selections=[{"kind": "map", "key": world.key}])
    assert next(item for item in preview["resources"] if item["kind"] == "map")["status"] == "reuse"
    assert reopened.import_resources(path, [{"kind": "map", "key": world.key}])["imported"] == []
    after = reopened.resource_set("map", world.key)
    assert after.resources == before.resources and after.files == before.files


@pytest.mark.parametrize("already_configured", [False, True])
def test_imported_model_first_credential_configures_original_experiment_without_rewriting_it(
        machines, tmp_path, monkeypatch, already_configured):
    from tests.test_portable_package_protocol import _experiment
    from generative_agents.ga_protocol.packages.definition import _experiment_definition, write_experiment_definition
    from generative_agents.ga_protocol.packages.io import write_integrity_manifest
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from generative_agents.ga_studio.resources.models import ModelService, ModelServiceInput
    database, exchange, store = machines("target")
    alias = "GA_IMPORTED_CHAT_TEST"
    monkeypatch.delenv(alias, raising=False)
    if already_configured:
        monkeypatch.setenv(alias, "existing-machine-value")
    experiment = _experiment(tmp_path / "source-package")
    definition = _experiment_definition(experiment)[1]
    definition["models"]["chat"]["credential_env"] = alias
    write_experiment_definition(experiment, definition)
    write_integrity_manifest(experiment)
    run = RunService().create(experiment, tmp_path / "run")
    original_manifest = (run / "experiment" / "integrity" / "sha256.json").read_bytes()
    records = read_resource_set(experiment)
    chat = next(record for record in records.resources if record.kind == "model" and "chat" in record.definition)
    imported = exchange.import_resources(experiment, [{"kind": "model", "key": chat.key}])["imported"][0]
    models = ModelService(database, store.root.parent)
    previous = models.detail(models.require(imported["id"]))
    assert previous["credential_configured"]["chat"] == already_configured
    config = previous["config"]["chat"]
    saved = models.update(imported["id"], ModelServiceInput(
        name=previous["name"], purpose="chat", base_url=config["base_url"], model=config["model"],
        api_key="target-secret-value", row_version=previous["row_version"]))
    saved_alias = saved["config"]["chat"]["credential_env"]
    assert (saved_alias == alias) is not already_configured
    assert models.credentials.resolve(saved_alias) == "target-secret-value"
    expected = "existing-machine-value" if already_configured else "target-secret-value"
    assert models.credentials.run_environment(run)[alias] == expected
    assert (run / "experiment" / "integrity" / "sha256.json").read_bytes() == original_manifest
    # Later key rotation preserves the first binding used by the original package.
    rotated = models.update(imported["id"], ModelServiceInput(
        name=previous["name"], purpose="chat", base_url=config["base_url"], model=config["model"],
        api_key="rotated-value", row_version=saved["row_version"]))
    assert rotated["config"]["chat"]["credential_env"] != saved_alias
    assert models.credentials.resolve(saved_alias) == "target-secret-value"
    assert models.credentials.run_environment(run)[alias] == expected
