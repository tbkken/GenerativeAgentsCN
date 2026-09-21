"""Mutable map resources and experiment map-selection boundaries."""
from __future__ import annotations

import copy
from pathlib import Path

from fastapi.testclient import TestClient

from tests.studio_support import create_test_studio


def test_map_catalog_is_a_single_mutable_resource_list(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        for index in range(6):
            response = client.post(
                "/api/studio/resources/maps",
                json={"name": f"分页地图 {index + 1}", "description": "地图列表 UX 验收"},
            )
            assert response.status_code == 201, response.text

        first_page = client.get("/api/studio/resources/maps?page=1&page_size=5").json()
        second_page = client.get("/api/studio/resources/maps?page=2&page_size=5").json()

    assert first_page["page_size"] == 5
    assert len(first_page["items"]) == 5
    assert first_page["total_pages"] >= 2
    assert second_page["items"]
    assert first_page["status_counts"] == {"ALL": first_page["total"]}
    assert all("current_draft" not in item for item in first_page["items"])
    assert all("current_published" not in item for item in first_page["items"])


def test_map_workspace_populates_experiment_creation_selector():
    javascript = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "map-workspace.js"
    ).read_text(encoding="utf-8")

    assert "newExperimentMap" in javascript
    assert "prepareExperimentCreate" in javascript
    assert "map_" + "revision_id" not in javascript
    assert "/draft/publish" not in javascript


def test_user_map_can_be_archived_restored_and_hard_deleted(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        created = client.post(
            "/api/studio/resources/maps",
            json={"name": "可清理地图", "description": "归档与删除验收"},
        ).json()
        archived = client.post(f"/api/studio/resources/maps/{created['id']}/archive")
        active = client.get("/api/studio/resources/maps?archived=active").json()
        archive_page = client.get("/api/studio/resources/maps?archived=archived").json()
        restored = client.post(f"/api/studio/resources/maps/{created['id']}/restore")
        deleted = client.delete(f"/api/studio/resources/maps/{created['id']}")
        missing = client.get(f"/api/studio/resources/maps/{created['id']}")

    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert created["id"] not in {item["id"] for item in active["items"]}
    assert created["id"] in {item["id"] for item in archive_page["items"]}
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_map_update_uses_optimistic_row_version(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        current = client.post("/api/studio/resources/maps", json={"name": "并发地图"}).json()
        first = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["lock_version"], "world": current["world"]},
        )
        assert first.status_code == 200, first.text
        stale = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["lock_version"], "world": current["world"]},
        )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MAP_CONFLICT"


def test_map_editor_document_survives_save_and_fresh_detail_reload(database_url):
    """地图保存响应和刷新后的详情都必须返回同一份完整 editor_v2 文档。"""
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        created = client.post("/api/studio/resources/maps", json={"name": "刷新回归地图"}).json()
        world = copy.deepcopy(created["world"])
        height, width = world["definition"]["size"]
        editor = {
            "schema_version": "ga-map-editor/v2",
            "root_node_id": "world-refresh-roundtrip",
            "material_sources": [],
            "material_slices": [],
            "material_canvases": [],
            "render_recipes": [],
            "visual_layers": [],
            "hierarchy_nodes": [{
                "id": "world-refresh-roundtrip",
                "kind": "WORLD",
                "parent_id": None,
                "name": "保存后的世界",
                "sort_order": 0,
                "bounds": {"x": 0, "y": 0, "width": width, "height": height},
                "semantic": "刷新后必须仍然存在的空间语义",
                "material_slice_id": None,
                "render_recipe_id": None,
                "render_mode": "LAYER_BACKED",
                "interaction_mode": "STATIC",
                "initial_state": {},
                "skill_bindings": [],
                "extensions": {},
            }],
            "import_metadata": {
                "importer": "refresh-regression/v2",
                "width": width,
                "height": height,
                "tile_size": world["definition"]["tile_size"],
            },
            "tile_overrides": {},
            "tile_override_parts": {},
            "tile_override_layers": {},
            "ui_state": {"roundtrip_marker": "map-refresh-regression"},
        }
        world["definition"]["editor_v2"] = editor
        editor["material_sources"].append({
            "id": "source-roundtrip",
            "name": "矩形素材原图",
            "kind": "GENERATED_COLOR",
            "asset_id": None,
            "asset_hash": None,
            "bundled_path": None,
            "generated_color": "#557766",
            "media_type": "image/png",
            "width_px": 96,
            "height_px": 32,
            "tile_width": 32,
            "tile_height": 32,
            "columns": 3,
            "rows": 1,
            "tile_count": 3,
            "margin": 0,
            "spacing": 0,
            "first_gid": None,
        })
        editor["material_slices"].append({
            "id": "slice-roundtrip",
            "source_id": "source-roundtrip",
            "name": "横向切片",
            "kind": "STAMP",
            "rotation_degrees": 0,
            "pixel_rect": {"x": 0, "y": 0, "width": 96, "height": 32},
            "grid_rect": {"x": 0, "y": 0, "width": 3, "height": 1},
            "trim_transparent": True,
            "indexed_gid": None,
            "local_tile_id": None,
            "readonly_indexed": False,
        })

        saved_response = client.put(
            f"/api/studio/resources/maps/{created['id']}",
            json={"row_version": created["lock_version"], "world": world},
        )
        assert saved_response.status_code == 200, saved_response.text
        saved = saved_response.json()
        refreshed_response = client.get(f"/api/studio/resources/maps/{created['id']}")
        assert refreshed_response.status_code == 200, refreshed_response.text
        refreshed = refreshed_response.json()

    from generative_agents.config.map_editor import MapEditorDocumentV2
    expected_editor = MapEditorDocumentV2.model_validate(editor).model_dump(mode="json", exclude_none=False)
    expected_editor["navigation"] = {"base_blocked": [], "overrides": {}}
    assert saved["world"]["definition"]["editor_v2"] == expected_editor
    assert refreshed["world"]["definition"]["editor_v2"] == expected_editor
    assert refreshed["world_hash"] == saved["world_hash"]
    assert refreshed["lock_version"] == saved["lock_version"]


def test_map_validation_reports_invalid_tiles_without_locking_map(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        current = client.post("/api/studio/resources/maps", json={"name": "无效地图"}).json()
        world = current["world"]
        world["world_key"] = "invalid-map"
        world["world_name"] = "无效地图"
        world["definition"] = {
            "world": "invalid-map",
            "size": [10, 10],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector"],
            "tiles": [{"coord": [11, 2], "address": ["越界"]}],
        }
        updated = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["lock_version"], "world": world},
        ).json()
        response = client.post(
            f"/api/studio/resources/maps/{current['id']}/validate",
            params={"row_version": updated["lock_version"]},
        )
        assert response.status_code == 200, response.text
        report = response.json()["validation"]
        assert report["valid"] is False
        assert report["errors"][0]["code"] == "WORLD_TILE_OUT_OF_BOUNDS"

        editable = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": updated["lock_version"], "world": updated["world"]},
        )
        assert editable.status_code == 200, editable.text


def test_map_validation_accepts_all_four_address_levels(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        current = client.post("/api/studio/resources/maps", json={"name": "Four-level map"}).json()
        world = current["world"]
        world["definition"] = {
            "world": "four-level-map",
            "size": [1, 1],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [{
                "coord": [0, 0],
                "collision": False,
                "address": ["four-level-map", "sector", "arena", "game-object"],
            }],
        }
        updated = client.put(
            f"/api/studio/resources/maps/{current['id']}",
            json={"row_version": current["lock_version"], "world": world},
        ).json()
        response = client.post(
            f"/api/studio/resources/maps/{current['id']}/validate",
            params={"row_version": updated["lock_version"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["validation"]["valid"] is True
