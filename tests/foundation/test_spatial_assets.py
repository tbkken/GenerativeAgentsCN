"""基础能力回归测试：覆盖 ``test_spatial_assets`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.config.spatial_assets import SpatialSceneExtension
from generative_agents.web import create_app


def _asset_by_key(client: TestClient, key: str) -> dict:
    """为本测试模块封装 ``_asset_by_key`` 辅助步骤，减少重复的场景搭建代码。"""
    result = client.get(f"/api/v1/spatial-assets?q={key}&page_size=100")
    assert result.status_code == 200, result.text
    return next(item for item in result.json()["items"] if item["asset_key"] == key)


def test_builtin_spatial_assets_cover_blocks_objects_zones_and_markings(database_url):
    """回归验证 ``test_builtin_spatial_assets_cover_blocks_objects_zones_and_markings`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/spatial-assets?page_size=100")
        assert response.status_code == 200, response.text
        builtins = [item for item in response.json()["items"] if item["is_builtin"]]
        assert len(builtins) == 9
        assert {item["asset_kind"] for item in builtins} == {
            "TILE",
            "OBJECT",
            "ZONE",
            "MARKING",
        }
        assert all(item["current_published"] for item in builtins)
        assert {"object-vehicle-gate", "zone-parking-slot"} <= {
            item["asset_key"] for item in builtins
        }
        traffic_light = next(
            item for item in builtins if item["asset_key"] == "object-traffic-light"
        )
        contract = traffic_light["active_contract"]
        assert contract["appearance"]["mode"] == "EMOJI"
        assert set(contract["appearance"]["state_variants"]) == {
            "vehicle-green",
            "vehicle-yellow",
            "vehicle-red",
        }
        assert contract["initial_state"] == {
            "state": "VEHICLE_GREEN",
            "phase": "VEHICLE_GREEN",
        }
        assert contract["skill_bindings"] == [
            {
                "interaction_key": "query-pedestrian-signal",
                "skill_name": "traffic-signal-state",
                "description": "查询当前行人是否可以安全通过斑马线",
                "interaction_radius_m": 2.5,
                "default_request": "请告诉我当前行人信号，以及现在是否可以过马路。",
            }
        ]
        assert "capability_attachments" not in contract


def test_public_map_can_opt_into_versioned_spatial_scene_without_changing_v1(database_url):
    """回归验证 ``test_public_map_can_opt_into_versioned_spatial_scene_without_changing_v1`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        road = _asset_by_key(client, "tile-road-asphalt")
        signal = _asset_by_key(client, "object-traffic-light")
        created = client.post(
            "/api/v1/maps",
            json={
                "name": "Spatial extension test map",
                "map_key": "spatial-extension-test-map",
                "width": 8,
                "height": 8,
                "tile_size": 16,
            },
        )
        assert created.status_code == 201, created.text
        public_map = created.json()
        draft = client.get(f"/api/v1/maps/{public_map['id']}/draft").json()
        world = deepcopy(draft["world"])
        world["definition"]["spatial_scene"] = {
            "schema_version": "ga-spatial-scene/v1",
            "meters_per_tile": 3.5,
            "palette_refs": {
                "road": road["current_published"]["id"],
            },
            "placements": [
                {
                    "instance_key": "north-signal",
                    "spatial_asset_revision_id": signal["current_published"]["id"],
                    "x_m": 12.0,
                    "y_m": 8.0,
                    "rotation_degrees": 0,
                    "state_overrides": {"phase": "vehicle-green"},
                }
            ],
        }
        saved = client.put(
            f"/api/v1/maps/{public_map['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": world},
        )
        assert saved.status_code == 200, saved.text
        published = client.post(
            f"/api/v1/maps/{public_map['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["world"]["definition"]["spatial_scene"][
            "schema_version"
        ] == "ga-spatial-scene/v1"

        legacy = client.get("/api/v1/maps?page=1&page_size=100").json()["items"]
        builtin = next(item for item in legacy if item["map_key"] == "the-ville")
        builtin_revision = client.get(
            f"/api/v1/maps/{builtin['id']}/revisions/{builtin['current_published']['id']}"
        ).json()
        assert "spatial_scene" not in builtin_revision["world"]["definition"]


def test_spatial_scene_contract_rejects_duplicate_placement_keys():
    """回归验证 ``test_spatial_scene_contract_rejects_duplicate_placement_keys`` 所描述的业务结果、故障边界和隔离约束。"""
    placement = {
        "instance_key": "signal-one",
        "spatial_asset_revision_id": "00000000-0000-0000-0000-000000000001",
        "x_m": 1,
        "y_m": 1,
    }
    try:
        SpatialSceneExtension.model_validate(
            {"placements": [placement, placement], "palette_refs": {}}
        )
    except ValueError as error:
        assert "instance keys must be unique" in str(error)
    else:
        raise AssertionError("duplicate placement keys should be rejected")


def test_map_workspace_exposes_spatial_asset_management_and_versioned_import():
    """回归验证 ``test_map_workspace_exposes_spatial_asset_management_and_versioned_import`` 所描述的业务结果、故障边界和隔离约束。"""
    static = Path(__file__).parents[2] / "generative_agents" / "web" / "static"
    html = (static / "experiment-console.html").read_text(encoding="utf-8")
    map_javascript = (static / "map-workspace.js").read_text(encoding="utf-8")
    asset_javascript = (static / "spatial-asset-workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'data-map-tab="assets"' in html
    assert 'id="spatialAssetGrid"' in html
    assert 'id="spatialStateVariantList"' in html
    assert 'id="spatialInitialStateList"' in html
    assert 'id="spatialCapabilityList"' not in html
    assert "spatial-asset-workspace:add-to-map" in map_javascript
    assert "definition.spatial_scene" in map_javascript
    assert "spatial_asset_revision_id" in map_javascript
    assert "readStateRows('initial')" in asset_javascript
    assert "capability_attachments" not in asset_javascript
