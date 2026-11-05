"""New 2D maps start empty; duplication is handled by resource exchange."""
from fastapi.testclient import TestClient

from tests.studio_support import create_test_studio


def test_new_map_is_blank_and_removed_copy_and_blueprint_inputs_are_rejected(database_url):
    with TestClient(create_test_studio(database_url=database_url)) as client:
        base = "/api/studio/resources/maps"
        created = client.post(base, json={"name": "空白画布", "map_key": "blank-canvas",
            "environment_type": "spatial_2d", "width": 12, "height": 8, "tile_size": 24})
        assert created.status_code == 201, created.text
        detail = created.json()
        definition = detail["world"]["definition"]
        assert definition["size"] == [8, 12] and definition["tile_size"] == 24
        assert len(definition["tiles"]) == 96
        assert all(not tile["collision"] and not tile["address"] for tile in definition["tiles"])
        assert not definition.get("editor", {}).get("build_guide")
        assert not definition.get("editor", {}).get("spatial_assets") and not detail["world"]["assets"]
        assert client.get("/api/studio/resources/skills").json()["items"] == []
        for removed in ({"source_map_id": detail["id"]}, {"blueprint_key": "two-day-commute"}):
            rejected = client.post(base, json={"name": "不应创建", **removed})
            assert rejected.status_code == 422
        assert client.get(base).json()["total"] == 1
        assert client.get("/api/studio/resources/map-blueprints").status_code == 404
        assert client.post(f"{base}/{detail['id']}/blueprint-steps/1", json={"row_version": 1}).status_code == 404

        definition["tiles"][0]["collision"] = True
        saved = client.put(f"{base}/{detail['id']}", json={"row_version": 1, "world": detail["world"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["world"]["definition"]["tiles"][0]["collision"]
        checked = client.post(f"{base}/{detail['id']}/validate", params={"row_version": saved.json()["row_version"]})
        assert checked.status_code == 200 and checked.json()["validation"]["valid"]
