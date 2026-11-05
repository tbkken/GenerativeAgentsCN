"""Local 3D map resources own metadata and reference only a SimWorld scene ID."""
import copy

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.packages.resources import read_resource_set
from generative_agents.ga_studio.resources.errors import ServiceError
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.maps import WorldMapService
from generative_agents.ga_studio.storage.assets import AssetStore
from tests.studio_support import create_test_studio


def test_scene_reference_maps_are_created_and_selected_as_local_resources(database_url):
    with TestClient(create_test_studio(database_url=database_url)) as client:
        base = "/api/studio/resources/maps"
        for scene in (None, "", "../remote-scene"):
            invalid = client.post(base, json={"name": "无效引用", "environment_type": "spatial_3d", "simworld_scene_id": scene})
            assert invalid.status_code == 422
        created = client.post(base, json={"name": "东京通勤", "map_key": "tokyo-commute", "description": "通勤实验背景",
            "environment_type": "spatial_3d", "simworld_scene_id": "map-15"})
        assert created.status_code == 201, created.text
        original = created.json()
        same_scene = client.post(base, json={"name": "东京配送", "map_key": "tokyo-delivery",
            "environment_type": "spatial_3d", "simworld_scene_id": "map-15"}).json()
        assert original["id"] != same_scene["id"] and original["editable"] is False
        assert original["world"]["definition"] == {"environment_type": "spatial_3d", "simworld_scene_id": "map-15"}
        assert not original["world"]["assets"] and original["description"] == "通勤实验背景"
        conflict = client.post(base, json={"name": "同键冲突", "map_key": "tokyo-commute",
            "environment_type": "spatial_3d", "simworld_scene_id": "map-2"})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "MAP_KEY_CONFLICT"
        tile = client.post(base, json={"name": "平面地图", "environment_type": "spatial_2d"}).json()
        chat = client.post(base, json={"name": "讨论", "environment_type": "social_1d"}).json()
        pages = [client.get(base, params={"for_experiment": True, "page_size": 1, "page": n}).json() for n in range(1, 5)]
        assert all(page["total"] == 4 and page["total_pages"] == 4 for page in pages)
        assert {page["items"][0]["id"] for page in pages} == {original["id"], same_scene["id"], tile["id"], chat["id"]}

        for world in (original["world"], tile["world"]):
            rejected = client.put(f"{base}/{original['id']}", json={"row_version": original["row_version"], "world": world})
            assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "SIMWORLD_MAP_READONLY"
        world = copy.deepcopy(original["world"])
        world["definition"]["roads"] = []
        assert client.put(f"{base}/{original['id']}", json={"row_version": original["row_version"], "world": world}).status_code == 422
        assert client.get(f"{base}/{original['id']}").json()["world"] == original["world"]
        checked = client.post(f"{base}/{original['id']}/validate", params={"row_version": original["row_version"]})
        assert checked.status_code == 200 and checked.json()["validation"]["valid"]
        assert checked.json()["validation"]["checks"][0]["code"] == "SIMWORLD_SCENE_REFERENCE"
        assert client.post(f"{base}/{original['id']}/archive").status_code == 200
        assert original["id"] not in {row["id"] for row in client.get(base, params={"for_experiment": True}).json()["items"]}
        assert client.post(f"{base}/{original['id']}/restore").status_code == 200
        assert client.delete(f"{base}/{original['id']}").status_code == 204
        assert client.get(f"{base}/{original['id']}").status_code == 404


def test_scene_reference_map_exchange_preserves_only_authored_metadata(database, tmp_path):
    maps = WorldMapService(database)
    detail = maps.create_map(name="夜间讨论背景", map_key="evening-scene", description="为夜间活动准备的场景引用",
        environment_type="spatial_3d", simworld_scene_id="map-15")
    with database.session_factory() as session:
        assert maps.materialize_validated_world(session, detail["id"]).definition == detail["world"]["definition"]
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path))
    archive = exchange.export_resource("map", detail["id"], tmp_path / "scene-reference.zip")
    resources = read_resource_set(archive)
    record = resources.resources[0]
    assert (record.name, record.key, record.description) == (detail["name"], detail["map_key"], detail["description"])
    assert record.definition == {"definition": detail["world"]["definition"], "assets": []}
    assert not record.attachments and not resources.files
    maps.set_archived(detail["id"], archived=True)
    with database.session_factory() as session, pytest.raises(ServiceError, match="归档"):
        maps.materialize_validated_world(session, detail["id"])
    maps.delete_map(detail["id"])
    assert exchange.preview_import(archive)["can_import"]
    imported = exchange.import_resources(archive, [{"kind": "map", "key": detail["map_key"]}])
    restored = maps.get_map(imported["imported"][0]["id"])
    assert restored["id"] != detail["id"]
    assert restored["description"] == detail["description"] and restored["world"] == detail["world"]
