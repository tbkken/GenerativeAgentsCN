"""基础能力回归测试：覆盖 ``test_map_build_blueprint`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_two_day_commute_blueprint_builds_one_publishable_map_step_by_step(
    database_url,
):
    """回归验证 ``test_two_day_commute_blueprint_builds_one_publishable_map_step_by_step`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        blueprints = client.get("/api/v1/map-blueprints")
        assert blueprints.status_code == 200, blueprints.text
        blueprint = next(
            item
            for item in blueprints.json()["items"]
            if item["key"] == "two-day-commute"
        )
        assert (blueprint["width"], blueprint["height"]) == (96, 56)
        assert [item["tool"] for item in blueprint["steps"]] == [
            "区域",
            "道路",
            "模块",
            "模块",
            "人行",
            "信号灯",
            "设施",
            "语义",
        ]

        created = client.post(
            "/api/v1/maps",
            json={
                "name": "两日通勤验收地图",
                "map_key": "commute-blueprint-acceptance",
                "blueprint_key": "two-day-commute",
                "width": 96,
                "height": 56,
                "tile_size": 32,
            },
        )
        assert created.status_code == 201, created.text
        public_map = created.json()
        draft = client.get(f"/api/v1/maps/{public_map['id']}/draft").json()
        definition = draft["world"]["definition"]
        assert definition["size"] == [56, 96]
        assert definition["editor"]["build_guide"]["current_step"] == 0
        assert definition["editor"]["build_guide"]["complete"] is False

        incomplete = client.post(
            f"/api/v1/maps/{public_map['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": draft["lock_version"],
            },
        )
        assert incomplete.status_code == 422, incomplete.text
        assert {
            item["code"]
            for item in incomplete.json()["error"]["details"]["errors"]
        } >= {"MAP_BLUEPRINT_INCOMPLETE"}

        out_of_order = client.post(
            f"/api/v1/maps/{public_map['id']}/draft/blueprint-steps/2",
            json={"lock_version": draft["lock_version"]},
        )
        assert out_of_order.status_code == 409, out_of_order.text
        assert out_of_order.json()["error"]["code"] == (
            "MAP_BLUEPRINT_STEP_OUT_OF_ORDER"
        )

        for step in range(1, 9):
            response = client.post(
                f"/api/v1/maps/{public_map['id']}/draft/blueprint-steps/{step}",
                json={"lock_version": draft["lock_version"]},
            )
            assert response.status_code == 200, response.text
            draft = response.json()
            assert draft["world"]["definition"]["editor"]["build_guide"][
                "current_step"
            ] == step

        definition = draft["world"]["definition"]
        guide = definition["editor"]["build_guide"]
        assert guide["complete"] is True
        editor_v2 = definition["editor_v2"]
        assert editor_v2["schema_version"] == "ga-map-editor/v2"
        assert {node["kind"] for node in editor_v2["hierarchy_nodes"]} == {
            "WORLD",
            "SECTOR",
            "ARENA",
            "GAME_OBJECT",
        }
        assert len(definition["editor"]["module_instances"]) == 2
        assert all(
            item["source_map_revision_id"] is None
            for item in definition["editor"]["module_instances"]
        )
        assert definition["traffic_layout"]["lanes_per_direction"] == 3
        assert definition["traffic_layout"]["crosswalk_count"] == 8
        assert {item["network_key"] for item in definition["navigation_networks"]} == {
            "vehicle-commute",
            "pedestrian-commute",
        }
        placements = definition["spatial_scene"]["placements"]
        assert len([item for item in placements if item["instance_key"].startswith("signal-")]) == 8
        assert len([item for item in placements if "-wait-" in item["instance_key"]]) == 8
        assert len([item for item in placements if item["instance_key"].startswith("parking-")]) == 3
        assert any(item["instance_key"] == "gate-office-entry" for item in placements)
        assert definition["commute_semantics"]["gate_credential"] == (
            "company.vehicle.enter"
        )

        published = client.post(
            f"/api/v1/maps/{public_map['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": draft["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        revision = published.json()
        assert revision["state"] == "PUBLISHED"
        assert revision["world_hash"]
        assert revision["world"]["world_key"] == "commute-blueprint-acceptance"
        signal_tile = next(
            tile
            for tile in revision["world"]["definition"]["tiles"]
            if tile["coord"] == [29, 22]
        )
        assert signal_tile["address"] == [
            "两日通勤验收地图",
            "城市道路",
            "东西向通勤主路",
            "路口 A north 信号灯",
        ]


def test_map_workspace_exposes_persisted_build_guide_controls():
    """回归验证 ``test_map_workspace_exposes_persisted_build_guide_controls`` 所描述的业务结果、故障边界和隔离约束。"""
    from pathlib import Path

    static = Path(__file__).parents[2] / "generative_agents" / "web" / "static"
    html = (static / "experiment-console.html").read_text(encoding="utf-8")
    javascript = (static / "map-workspace.js").read_text(encoding="utf-8")
    editor_javascript = (static / "map-editor-v2.js").read_text(encoding="utf-8")
    stylesheet = (static / "map-workspace.css").read_text(encoding="utf-8")

    assert 'id="newMapBlueprint"' in html
    assert 'id="mapBuildGuide"' in editor_javascript
    assert 'id="applyMapBlueprintStep"' in editor_javascript
    assert "map-editor-v2:apply-blueprint-step" in editor_javascript
    assert "grid-template-rows: 54px auto minmax(0, 1fr)" in stylesheet
    assert "async applyBlueprintStep()" in javascript
    assert "/draft/blueprint-steps/${nextStep}" in javascript
    assert "current_step" in javascript
