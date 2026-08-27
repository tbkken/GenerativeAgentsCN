"""基础能力回归测试：覆盖 ``test_world_maps`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_map_catalog_supports_status_filters_and_five_item_pages(database_url):
    """回归验证 ``test_map_catalog_supports_status_filters_and_five_item_pages`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        for index in range(6):
            response = client.post(
                "/api/v1/maps",
                json={"name": f"分页地图 {index + 1}", "description": "地图列表 UX 验收"},
            )
            assert response.status_code == 201, response.text

        first_page = client.get("/api/v1/maps?page=1&page_size=5").json()
        second_page = client.get("/api/v1/maps?page=2&page_size=5").json()
        assert first_page["page_size"] == 5
        assert len(first_page["items"]) == 5
        assert first_page["total_pages"] >= 2
        assert second_page["items"]
        assert first_page["status_counts"]["ALL"] == first_page["total"]
        assert (
            first_page["status_counts"]["DRAFT"]
            + first_page["status_counts"]["PUBLISHED"]
            == first_page["status_counts"]["ALL"]
        )

        drafts = client.get("/api/v1/maps?status=DRAFT&page=1&page_size=5").json()
        assert drafts["total"] == drafts["status_counts"]["DRAFT"]
        assert all(item["status"] == "DRAFT" for item in drafts["items"])

        invalid = client.get("/api/v1/maps?status=ARCHIVED")
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_MAP_STATUS"


def test_map_workspace_populates_experiment_creation_selector():
    """回归验证 ``test_map_workspace_populates_experiment_creation_selector`` 所描述的业务结果、故障边界和隔离约束。"""
    javascript = (
        __import__("pathlib").Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "map-workspace.js"
    ).read_text(encoding="utf-8")

    assert "newExperimentMap" in javascript
    assert "prepareExperimentCreate" in javascript


def test_public_map_lifecycle_and_experiment_overlay_are_isolated(database_url):
    """回归验证 ``test_public_map_lifecycle_and_experiment_overlay_are_isolated`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/maps").json()
        assert catalog["total"] >= 2
        builtin = next(item for item in catalog["items"] if item["map_key"] == "the-ville")
        assert builtin["map_key"] == "the-ville"
        assert builtin["current_published"]["state"] == "PUBLISHED"

        created_response = client.post(
            "/api/v1/maps",
            json={"name": "共享测试地图", "description": "供多个实验复用"},
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        draft = client.get(f"/api/v1/maps/{created['id']}/draft").json()
        world = draft["world"]
        world["world_key"] = "shared-test-map"
        world["world_name"] = "共享测试地图"
        world["definition"] = {
            "world": "shared-test-map",
            "tile_size": 32,
            "size": [2, 2],
            "map": [[0, 0], [0, 0]],
            "camera": [0, 0],
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [
                {"coord": [0, 0], "collision": False},
                {"coord": [1, 0], "collision": False},
                {"coord": [0, 1], "collision": False},
                {"coord": [1, 1], "collision": False},
            ],
        }
        saved = client.put(
            f"/api/v1/maps/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": world},
        )
        assert saved.status_code == 200, saved.text
        published = client.post(
            f"/api/v1/maps/{created['id']}/draft/publish",
            json={
                "draft_revision_id": saved.json()["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        published_revision = published.json()
        fetched_revision = client.get(
            f"/api/v1/maps/{created['id']}/revisions/{published_revision['id']}"
        )
        assert fetched_revision.status_code == 200
        assert fetched_revision.json()["world"]["definition"]["world"] == "shared-test-map"

        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "地图覆盖层实验", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        experiment_draft = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft"
        ).json()
        selected = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/map",
            json={
                "lock_version": experiment_draft["lock_version"],
                "map_revision_id": published_revision["id"],
            },
        )
        assert selected.status_code == 200, selected.text
        selected_world = selected.json()["definition"]["world"]
        assert selected_world["map_id"] == created["id"]
        assert selected_world["map_revision_id"] == published_revision["id"]

        overlaid = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/map-overlay",
            json={
                "lock_version": selected.json()["lock_version"],
                "overlay": {
                    "definition_patch": {"editor": {"experiment_note": "only here"}},
                    "asset_additions": [],
                    "removed_asset_paths": [],
                },
            },
        )
        assert overlaid.status_code == 200, overlaid.text
        assert (
            overlaid.json()["definition"]["world"]["definition"]["editor"]
            ["experiment_note"]
            == "only here"
        )

        forked = client.post(
            f"/api/v1/maps/{created['id']}/revisions/{published_revision['id']}/fork"
        )
        assert forked.status_code == 201, forked.text
        assert "editor" not in forked.json()["world"]["definition"]

        refreshed = client.get(f"/api/v1/maps/{created['id']}").json()
        assert refreshed["usage_count"] == 1


def test_map_draft_uses_optimistic_locking(database_url):
    """回归验证 ``test_map_draft_uses_optimistic_locking`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post("/api/v1/maps", json={"name": "并发地图"}).json()
        draft = client.get(f"/api/v1/maps/{created['id']}/draft").json()
        first = client.put(
            f"/api/v1/maps/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": draft["world"]},
        )
        assert first.status_code == 200
        stale = client.put(
            f"/api/v1/maps/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": draft["world"]},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "MAP_REVISION_CONFLICT"


def test_map_publish_rejects_runtime_invalid_tiles(database_url):
    """回归验证 ``test_map_publish_rejects_runtime_invalid_tiles`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post("/api/v1/maps", json={"name": "无效地图"}).json()
        draft = client.get(f"/api/v1/maps/{created['id']}/draft").json()
        world = draft["world"]
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
            f"/api/v1/maps/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": world},
        ).json()

        response = client.post(
            f"/api/v1/maps/{created['id']}/draft/publish",
            json={
                "draft_revision_id": updated["id"],
                "lock_version": updated["lock_version"],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "MAP_VALIDATION_FAILED"
        errors = response.json()["error"]["details"]["errors"]
        assert errors[0]["code"] == "WORLD_TILE_OUT_OF_BOUNDS"


def test_map_publish_accepts_address_using_every_declared_level(database_url):
    """回归验证 ``test_map_publish_accepts_address_using_every_declared_level`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post("/api/v1/maps", json={"name": "Four-level map"}).json()
        draft = client.get(f"/api/v1/maps/{created['id']}/draft").json()
        world = draft["world"]
        world["definition"] = {
            "world": "four-level-map",
            "size": [1, 1],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [
                {
                    "coord": [0, 0],
                    "collision": False,
                    "address": [
                        "four-level-map",
                        "sector",
                        "arena",
                        "game-object",
                    ],
                }
            ],
        }
        updated = client.put(
            f"/api/v1/maps/{created['id']}/draft",
            json={"lock_version": draft["lock_version"], "world": world},
        ).json()

        response = client.post(
            f"/api/v1/maps/{created['id']}/draft/publish",
            json={
                "draft_revision_id": updated["id"],
                "lock_version": updated["lock_version"],
            },
        )

        assert response.status_code == 200, response.text
