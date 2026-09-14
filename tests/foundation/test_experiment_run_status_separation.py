"""Experiment lifecycle and live Run status must remain separate file projections."""
from fastapi.testclient import TestClient

from generative_agents.ga_protocol import atomic_write_json, read_json
from generative_agents.ga_runtime.service import RunService
from generative_agents.ga_studio.web import create_studio_app
from tests.test_portable_package_protocol import _experiment


def test_lifecycle_does_not_follow_stale_catalog_or_resumed_run(tmp_path):
    var = tmp_path / "var"
    experiment = _experiment(var / "packages")
    experiment_id = read_json(experiment / "manifest.json")["experiment"]["experiment_id"]
    run_root = RunService().create(experiment, var / "packages/runs/current")
    status = read_json(run_root / "status.json")
    status["status"] = "PAUSED"
    atomic_write_json(run_root / "status.json", status)
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.db').as_posix()}", var_dir=var)
    url = f"/api/studio/experiments/{experiment_id}"
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild").raise_for_status()
        draft = client.get(url).json()
        assert draft["status"] == "DRAFT" and draft["editable"]
        assert draft["latest_run"]["status"] == "PAUSED"
        client.post(url + "/seal").raise_for_status()
        for live_state in ("QUEUED", "RUNNING", "PAUSED", "RUNNING", "COMPLETED"):
            status["status"] = live_state
            atomic_write_json(run_root / "status.json", status)
            detail = client.get(url).json()
            assert detail["status"] == "SEALED" and not detail["editable"]
            assert detail["current_published"]["state"] == "SEALED"
            assert detail["latest_run"]["status"] == live_state
            assert client.get(url + "/runs").json()["items"][0]["status"] == live_state
            assert client.get(f"/api/studio/runs/{status['run_id']}").json()["status"] == live_state
            listing = client.get("/api/studio/experiments").json()
            assert listing["total"] == 1
            assert listing["items"][0]["status"] == "SEALED"
            assert listing["items"][0]["latest_run"]["status"] == live_state
        # The index intentionally remains PAUSED: the UI must not use it as Run truth.
        records = client.get("/api/studio/packages?kind=run").json()["items"]
        assert records[0]["status"] == "PAUSED"


def test_selected_historical_run_keeps_its_own_status(tmp_path):
    var = tmp_path / "var"
    experiment = _experiment(var / "packages")
    experiment_id = read_json(experiment / "manifest.json")["experiment"]["experiment_id"]
    roots = [RunService().create(experiment, var / f"packages/runs/{name}") for name in ("older", "newer")]
    states = {}
    for root, value in zip(roots, ("PAUSED", "COMPLETED")):
        status = read_json(root / "status.json")
        status["status"] = value
        atomic_write_json(root / "status.json", status)
        states[status["run_id"]] = value
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.db').as_posix()}", var_dir=var)
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild").raise_for_status()
        client.post(f"/api/studio/experiments/{experiment_id}/seal").raise_for_status()
        runs = client.get(f"/api/studio/experiments/{experiment_id}/runs").json()["items"]
        assert {item["run_id"]: item["status"] for item in runs} == states
        for run_id, expected in states.items():
            assert client.get(f"/api/studio/runs/{run_id}").json()["status"] == expected
            assert client.get(f"/api/studio/experiments/{experiment_id}").json()["status"] == "SEALED"
