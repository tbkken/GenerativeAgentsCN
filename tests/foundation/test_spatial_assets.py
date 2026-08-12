from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.config.spatial_assets import SpatialSceneExtension
from generative_agents.web import create_app


def _asset_by_key(client: TestClient, key: str) -> dict:
    result = client.get(f"/api/v1/spatial-assets?q={key}&page_size=100")
    assert result.status_code == 200, result.text
    return next(item for item in result.json()["items"] if item["asset_key"] == key)


def _capability_by_key(client: TestClient, key: str) -> dict:
    result = client.get(f"/api/v1/capabilities?q={key}&page_size=100")
    assert result.status_code == 200, result.text
    return next(item for item in result.json()["items"] if item["capability_key"] == key)


def test_builtin_spatial_assets_cover_blocks_objects_zones_and_markings(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/spatial-assets?page_size=100")
        assert response.status_code == 200, response.text
        builtins = [item for item in response.json()["items"] if item["is_builtin"]]
        assert len(builtins) == 7
        assert {item["asset_kind"] for item in builtins} == {
            "TILE",
            "OBJECT",
            "ZONE",
            "MARKING",
        }
        assert all(item["current_published"] for item in builtins)
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
        signal_controller = _capability_by_key(client, "traffic-signal-cycle")
        attachment = contract["capability_attachments"][0]
        assert attachment["attachment_key"] == "signal-cycle"
        assert attachment["capability_revision_id"] == signal_controller[
            "current_published"
        ]["id"]
        assert attachment["output_bindings"] == {
            "signal_state": "state:${target}:signal"
        }


def test_spatial_asset_can_attach_published_perception_capability(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        base = _asset_by_key(client, "zone-pedestrian-wait")
        perception = _capability_by_key(client, "spatial-zone-presence")
        created = client.post(
            "/api/v1/spatial-assets",
            json={
                "name": "Smart pedestrian wait zone",
                "asset_key": "smart-pedestrian-wait-zone-test",
                "source_revision_id": base["current_published"]["id"],
            },
        )
        assert created.status_code == 201, created.text
        asset = created.json()
        draft = client.get(f"/api/v1/spatial-assets/{asset['id']}/draft").json()
        contract = deepcopy(draft["contract"])
        contract["name"] = "Smart pedestrian wait zone"
        contract["capability_attachments"] = [
            {
                "attachment_key": "presence-sensor",
                "capability_revision_id": perception["current_published"]["id"],
                "capability_bundle_revision_id": None,
                "parameters": {"entity_types": ["PEDESTRIAN"], "debounce_ms": 200},
                "enabled": True,
            }
        ]
        saved = client.put(
            f"/api/v1/spatial-assets/{asset['id']}/draft",
            json={"lock_version": draft["lock_version"], "contract": contract},
        )
        assert saved.status_code == 200, saved.text
        published = client.post(
            f"/api/v1/spatial-assets/{asset['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["readonly"] is True
        assert published.json()["validation"]["valid"] is True


def test_spatial_asset_rejects_capability_with_wrong_mount_target(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        base = _asset_by_key(client, "object-traffic-light")
        walking = _capability_by_key(client, "mobility-continuous-walk")
        created = client.post(
            "/api/v1/spatial-assets",
            json={
                "name": "Invalid walking signal",
                "asset_key": "invalid-walking-signal-test",
                "source_revision_id": base["current_published"]["id"],
            },
        ).json()
        draft = client.get(f"/api/v1/spatial-assets/{created['id']}/draft").json()
        contract = draft["contract"]
        contract["capability_attachments"] = [
            {
                "attachment_key": "walk-action",
                "capability_revision_id": walking["current_published"]["id"],
                "capability_bundle_revision_id": None,
                "parameters": {"speed_mps": 1.2, "max_acceleration_mps2": 1.0},
                "enabled": True,
            }
        ]
        saved = client.put(
            f"/api/v1/spatial-assets/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "contract": contract},
        )
        assert saved.status_code == 200, saved.text
        rejected = client.post(
            f"/api/v1/spatial-assets/{created['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert rejected.status_code == 422
        codes = {
            item["code"] for item in rejected.json()["error"]["details"]["errors"]
        }
        assert "SPATIAL_CAPABILITY_TARGET_MISMATCH" in codes


def test_public_map_can_opt_into_versioned_spatial_scene_without_changing_v1(database_url):
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
                    "capability_parameter_overrides": {},
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
    assert 'id="spatialCapabilityList"' in html
    assert "spatial-asset-workspace:add-to-map" in map_javascript
    assert "definition.spatial_scene" in map_javascript
    assert "spatial_asset_revision_id" in map_javascript
    assert "parameterFields(schema, parameters)" in asset_javascript
