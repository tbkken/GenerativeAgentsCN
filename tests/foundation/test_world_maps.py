from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_public_map_lifecycle_and_experiment_overlay_are_isolated(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/maps").json()
        assert catalog["total"] == 1
        builtin = catalog["items"][0]
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
