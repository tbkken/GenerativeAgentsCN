"""Mutable spatial assets and their live map references."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.schemas.spatial_assets import SpatialSceneExtension
from tests.studio_support import create_test_studio


def _asset_by_key(client: TestClient, key: str) -> dict:
    result = client.get(f"/api/studio/resources/spatial-assets?q={key}&page_size=100")
    assert result.status_code == 200, result.text
    return next(item for item in result.json()["items"] if item["asset_key"] == key)


def test_builtin_spatial_assets_are_directly_editable_resources(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        response = client.get("/api/studio/resources/spatial-assets?page_size=100")
        assert response.status_code == 200, response.text
        builtins = [item for item in response.json()["items"] if item["is_builtin"]]
        traffic_light = next(
            item for item in builtins if item["asset_key"] == "object-traffic-light"
        )
        detail = client.get(f"/api/studio/resources/spatial-assets/{traffic_light['id']}").json()
        changed = deepcopy(detail["contract"])
        changed["summary"] = "可以直接修改的信号灯"
        updated = client.put(
            f"/api/studio/resources/spatial-assets/{traffic_light['id']}",
            json={"row_version": detail["row_version"], "contract": changed},
        )

    assert len(builtins) == 9
    assert {item["asset_kind"] for item in builtins} == {
        "TILE", "OBJECT", "ZONE", "MARKING"
    }
    assert all("current_draft" not in item for item in builtins)
    assert all("current_published" not in item for item in builtins)
    assert updated.status_code == 200, updated.text
    assert updated.json()["contract"]["summary"] == "可以直接修改的信号灯"
    assert updated.json()["row_version"] == detail["row_version"] + 1


def test_custom_and_seeded_spatial_assets_can_be_deleted_permanently(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        created = client.post(
            "/api/studio/resources/spatial-assets",
            json={"name": "临时路标", "asset_key": "temporary-road-sign", "asset_kind": "OBJECT"},
        ).json()
        assert client.delete(f"/api/studio/resources/spatial-assets/{created['id']}").status_code == 204
        assert client.get(f"/api/studio/resources/spatial-assets/{created['id']}").status_code == 404
        builtin = _asset_by_key(client, "object-traffic-light")
        assert client.delete(f"/api/studio/resources/spatial-assets/{builtin['id']}").status_code == 204

    restarted = create_test_studio(database_url=database_url)
    with TestClient(restarted) as client:
        keys = {
            item["asset_key"]
            for item in client.get("/api/studio/resources/spatial-assets?page_size=100").json()["items"]
        }
    assert "object-traffic-light" not in keys


def test_asset_edits_immediately_change_maps_that_reference_the_asset(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        road = _asset_by_key(client, "tile-road-asphalt")
        signal = _asset_by_key(client, "object-traffic-light")
        current = client.post(
            "/api/studio/resources/maps",
            json={"name": "实时素材地图", "map_key": "live-asset-map", "width": 8, "height": 8},
        ).json()
        world = deepcopy(current["world"])
        world["definition"]["spatial_scene"] = {
            "schema_version": "ga-spatial-scene/v2",
            "palette_refs": {"road": road["id"]},
            "placements": [{
                "instance_key": "north-signal",
                "spatial_asset_id": signal["id"],
                "x_tiles": 6.0,
                "y_tiles": 4.0,
                "rotation_degrees": 0,
                "state_overrides": {"phase": "vehicle-green"},
            }],
        }
        saved = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["row_version"], "world": world},
        )
        assert saved.status_code == 200, saved.text
        checked = client.post(
            f"/api/studio/resources/maps/{current['id']}/validate",
            params={"row_version": saved.json()["row_version"]},
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["validation"] is not None

        road_detail = client.get(f"/api/studio/resources/spatial-assets/{road['id']}").json()
        contract = deepcopy(road_detail["contract"])
        contract["appearance"]["color"] = "#123456"
        changed = client.put(
            f"/api/studio/resources/spatial-assets/{road['id']}",
            json={"row_version": road_detail["row_version"], "contract": contract},
        )
        assert changed.status_code == 200, changed.text

        refreshed_map = client.get(f"/api/studio/resources/maps/{current['id']}").json()

    assert refreshed_map["world"]["definition"]["editor"]["spatial_assets"][road["id"]]["appearance"]["color"] == "#123456"
    assert refreshed_map["validation"] is None
    assert changed.json()["usage_count"] == 1


def test_deleting_used_asset_is_allowed_and_map_validation_reports_missing_reference(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        road = _asset_by_key(client, "tile-road-asphalt")
        current = client.post("/api/studio/resources/maps", json={"name": "可缺失素材地图"}).json()
        world = deepcopy(current["world"])
        world["definition"]["spatial_scene"] = {
            "schema_version": "ga-spatial-scene/v2",
            "palette_refs": {"road": road["id"]},
            "placements": [],
        }
        saved = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["row_version"], "world": world},
        ).json()
        deleted = client.delete(f"/api/studio/resources/spatial-assets/{road['id']}")
        validated = client.post(
            f"/api/studio/resources/maps/{current['id']}/validate",
            params={"row_version": saved["row_version"]},
        )

    assert deleted.status_code == 204
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation"]["valid"] is False
    assert "SPATIAL_ASSET_UNAVAILABLE" in {
        item["code"] for item in validated.json()["validation"]["errors"]
    }


def test_spatial_scene_contract_rejects_duplicate_placement_keys():
    placement = {
        "instance_key": "signal-one",
        "spatial_asset_id": "00000000-0000-0000-0000-000000000001",
        "x_tiles": 1,
        "y_tiles": 1,
    }
    with pytest.raises(ValueError, match="instance keys must be unique"):
        SpatialSceneExtension.model_validate(
            {"placements": [placement, placement], "palette_refs": {}}
        )


def test_map_workspace_exposes_live_spatial_asset_management():
    static = Path(__file__).parents[2] / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static'
    html = (static / "shell/experiment-console.html").read_text(encoding="utf-8")
    map_javascript = (static / "resources/map-workspace.js").read_text(encoding="utf-8")
    editor_javascript = (static / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    asset_javascript = (static / "resources/spatial-asset-workspace.js").read_text(encoding="utf-8")

    assert 'data-map-tab="assets"' in html
    assert 'id="spatialAssetGrid"' in html
    assert "spatial-asset-workspace:add-to-map" in map_javascript
    assert "spatial-asset-workspace:updated" in map_javascript
    assert "drawSpatialAssets(ctx, tile)" in editor_javascript
    assert "spatial_asset_id" in map_javascript
    assert "spatial_asset_" + "revision_id" not in map_javascript
    assert "readStateRows('initial')" in asset_javascript
    assert "/draft/publish" not in asset_javascript
