"""The experiment centre keeps fixed pagination and archive management only."""
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.adapters.web.app import create_studio_app
from tests.test_portable_package_protocol import _experiment


def test_fixed_pagination_archive_restore_and_metadata(tmp_path):
    var_dir = tmp_path / "var"
    experiment = _experiment(var_dir / "packages")
    original_id = read_json(experiment / "manifest.json")["experiment"]["experiment_id"]
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.db').as_posix()}", var_dir=var_dir)
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild").raise_for_status()
        ids = [original_id]
        for _ in range(6):
            copy = client.post(f"/api/studio/experiments/{original_id}/duplicate", json={})
            copy.raise_for_status()
            ids.append(copy.json()["experiment_id"])
        presentation_path = var_dir / "studio-presentation.json"
        retired_view = {"id": "existing-view", "name": "历史视图", "query": {"owner": "教师"}}
        atomic_write_json(presentation_path, {
            "schema_version": 1,
            "experiments": {original_id: {"owner": "教师", "tags": ["教材", "阅读"]}},
            "saved_views": [retired_view],
        })
        first = client.get("/api/studio/experiments").json()
        second = client.get("/api/studio/experiments?page=2").json()
        assert first["page_size"] == second["page_size"] == 5
        assert first["total"] == 7 and first["total_pages"] == 2
        assert len(first["items"]) == 5 and len(second["items"]) == 2
        ordered = first["items"] + second["items"]
        assert {item["id"] for item in ordered} == set(ids)
        assert [item["updated_at"] for item in ordered] == sorted((item["updated_at"] for item in ordered), reverse=True)
        before = {item["id"]: (item["owner"], item["tags"]) for item in ordered}
        batch = {"experiment_ids": ids[:2], "action": "ARCHIVE"}
        assert client.post("/api/studio/experiments/batch", json=batch).json()["affected"] == 2
        assert client.get("/api/studio/experiments").json()["total"] == 5
        archived = client.get("/api/studio/experiments?archived=archived").json()
        assert {item["id"] for item in archived["items"]} == set(ids[:2])
        assert all((item["owner"], item["tags"]) == before[item["id"]] for item in archived["items"])
        batch["action"] = "RESTORE"
        assert client.post("/api/studio/experiments/batch", json=batch).json()["affected"] == 2
        assert client.get("/api/studio/experiments").json()["total"] == 7
        assert client.get("/api/studio/experiments?archived=archived").json()["total"] == 0
        assert read_json(presentation_path)["experiments"][original_id]["owner"] == "教师"
        assert read_json(presentation_path)["experiments"][original_id]["tags"] == ["教材", "阅读"]
        assert read_json(presentation_path)["saved_views"] == [retired_view]
        assert client.get("/api/studio/experiments?page_size=10").status_code == 422
        assert client.get("/api/studio/experiments?page=0").status_code == 422


def test_removed_list_features_have_no_routes_or_query_contract(tmp_path):
    factories = [create_studio_app]
    for index, factory in enumerate(factories):
        kwargs = {"database_url": f"sqlite:///{(tmp_path / f'studio-{index}.db').as_posix()}", "var_dir": tmp_path / f"var-{index}"}
        app = factory(**kwargs)
        paths = app.openapi()["paths"]
        assert not any("experiment-saved-views" in path or "experiment-comparison-groups" in path or path.endswith("/experiments/compare") for path in paths)
        for path in ["/api/studio/experiments", "/api/v1/experiments"]:
            if path in paths:
                assert {param["name"] for param in paths[path]["get"]["parameters"]} == {"page", "page_size", "archived"}
        # Validation rejects retired batch actions before any endpoint or lifespan work.
        client = TestClient(app)
        try:
            for prefix in (["/api/studio"] if factory is create_studio_app else ["/api/studio", "/api/v1"]):
                for action in ["ADD_TAGS", "SET_OWNER"]:
                    response = client.post(prefix + "/experiments/batch", json={"experiment_ids": ["unused"], "action": action})
                    assert response.status_code == 422
        finally:
            client.close()
