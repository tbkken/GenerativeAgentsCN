from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from generative_agents.persistence.models import ExperimentRevision, Run, RunEvent
from generative_agents.web import create_app


def test_experiment_api_create_list_validate_and_conflict(database_url):
    app = create_app(database_url=database_url)
    with TestClient(app) as client:
        created_response = client.post(
            "/api/v1/experiments",
            json={
                "name": "API Experiment",
                "goal": "Exercise the real service",
                "source": {"type": "BLANK"},
            },
        )
        assert created_response.status_code == 201
        assert created_response.headers["X-Request-ID"]
        created = created_response.json()

        listing = client.get("/api/v1/experiments", params={"page_size": 10}).json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == created["id"]

        draft = client.get(f"/api/v1/experiments/{created['id']}/draft").json()
        simulation = draft["definition"]["simulation"]
        simulation["random_seed"] = 99
        saved = client.patch(
            f"/api/v1/experiments/{created['id']}/draft/simulation",
            json={"lock_version": 1, "data": simulation},
        )
        assert saved.status_code == 200
        assert saved.json()["lock_version"] == 2

        stale = client.patch(
            f"/api/v1/experiments/{created['id']}/draft/simulation",
            json={"lock_version": 1, "data": simulation},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        report = client.post(
            f"/api/v1/experiments/{created['id']}/draft/validate"
        ).json()
        assert report["valid"] is False
        assert report["errors"]


def test_api_errors_have_uniform_envelope_and_request_id(database_url):
    app = create_app(database_url=database_url)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/experiments/not-found", headers={"X-Request-ID": "known-request"}
        )
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "EXPERIMENT_NOT_FOUND",
                "message": "experiment 不存在",
                "details": {"id": "not-found"},
                "request_id": "known-request",
            }
        }

        invalid = client.post(
            "/api/v1/experiments", json={"name": "X", "unexpected": True}
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_metadata_agent_prompt_and_world_draft_routes_are_optimistic(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={"name": "Editable", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        renamed = client.patch(
            f"/api/v1/experiments/{created['id']}",
            json={"row_version": created["row_version"], "name": "Renamed", "goal": "G"},
        )
        assert renamed.status_code == 200
        draft = client.get(f"/api/v1/experiments/{created['id']}/draft").json()
        assert draft["definition"]["experiment"]["name"] == "Renamed"

        agent = draft["definition"]["agents"][0]
        patched = client.patch(
            f"/api/v1/experiments/{created['id']}/draft/agents/{agent['agent_key']}",
            json={"lock_version": draft["lock_version"], "data": {"currently": "isolated"}},
        ).json()
        assert patched["definition"]["agents"][0]["currently"] == "isolated"

        prompt = client.put(
            f"/api/v1/experiments/{created['id']}/draft/prompts/base_desc",
            json={"lock_version": patched["lock_version"], "data": {"content": "独立正文"}},
        ).json()
        assert prompt["definition"]["prompts"]["base_desc"]["content"] == "独立正文"

        world = prompt["definition"]["world"]
        world["world_name"] = "Owned world"
        saved_world = client.put(
            f"/api/v1/experiments/{created['id']}/draft/world",
            json={"lock_version": prompt["lock_version"], "data": world},
        )
        assert saved_world.status_code == 200
        assert saved_world.json()["definition"]["world"]["world_name"] == "Owned world"


def test_duplicate_experiment_deep_copies_the_selected_definition(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        source = client.post(
            "/api/v1/experiments",
            json={"name": "Source", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        duplicate_response = client.post(
            f"/api/v1/experiments/{source['id']}/duplicate", json={}
        )
        assert duplicate_response.status_code == 201
        duplicate = duplicate_response.json()
        assert duplicate["id"] != source["id"]
        assert duplicate["name"] == "Source · 副本"
        source_draft = client.get(f"/api/v1/experiments/{source['id']}/draft").json()
        copied_draft = client.get(f"/api/v1/experiments/{duplicate['id']}/draft").json()
        assert copied_draft["definition"]["world"] == source_draft["definition"]["world"]

        changed = copied_draft["definition"]["simulation"]
        changed["random_seed"] = 919
        client.patch(
            f"/api/v1/experiments/{duplicate['id']}/draft/simulation",
            json={"lock_version": copied_draft["lock_version"], "data": changed},
        )
        assert client.get(f"/api/v1/experiments/{source['id']}/draft").json()["definition"]["simulation"]["random_seed"] != 919


def test_asset_and_secret_http_contracts_are_safe_and_idempotent(database_url):
    app = create_app(database_url=database_url)
    with TestClient(app) as client:
        payload = b'{"world":"http-test"}'
        first = client.post(
            "/api/v1/assets",
            files={"file": ("maze.json", payload, "application/json")},
        )
        second = client.post(
            "/api/v1/assets",
            files={"file": ("copy.json", payload, "application/json")},
        )
        assert first.status_code == 201
        assert second.json()["asset_id"] == first.json()["asset_id"]
        assert "relative_path" not in first.json()

        content = client.get(
            f"/api/v1/assets/{first.json()['asset_id']}/content"
        )
        assert content.content == payload
        assert content.headers["etag"] == f'"{first.json()["sha256"]}"'
        assert client.get(
            f"/api/v1/assets/{first.json()['asset_id']}/content",
            headers={"If-None-Match": content.headers["etag"]},
        ).status_code == 304

        invalid = client.post(
            "/api/v1/assets",
            files={"file": ("notes.md", b"# not an allowed asset", "text/markdown")},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_ASSET"

        secret = client.post(
            "/api/v1/secrets",
            json={"kind": "OPENAI_API_KEY", "value": "sk-http-secret"},
        )
        replacement = client.post(
            f"/api/v1/secrets/{secret.json()['secret_id']}/replacement",
            json={"kind": "OPENAI_API_KEY", "value": "sk-http-replacement"},
        )
        assert secret.status_code == replacement.status_code == 201
        assert replacement.json()["supersedes_id"] == secret.json()["secret_id"]
        assert "value" not in secret.text
        assert "sk-http" not in secret.text + replacement.text


def test_health_endpoint_checks_database_connectivity(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_global_event_cursor_exposes_run_activity_with_experiment_identity(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "Live status", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        run_id = "global-event-run"
        now = datetime.now(timezone.utc)
        with app.state.database.session_factory.begin() as session:
            revision = session.get(
                ExperimentRevision, experiment["current_draft"]["id"]
            )
            revision.state = "PUBLISHED"
            revision.snapshot_complete = True
            revision.published_at = now
            session.flush()
            session.add(
                Run(
                    id=run_id,
                    experiment_id=experiment["id"],
                    revision_id=experiment["current_draft"]["id"],
                    status="QUEUED",
                    queued_at=now,
                    requested_steps=10,
                    completed_steps=0,
                    recoverable_step=0,
                    stride_minutes=10,
                    virtual_time=now,
                    run_dir=f"runs/{run_id}",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                RunEvent(
                    run_id=run_id,
                    event_type="state",
                    payload_json={"status": "RUNNING"},
                    created_at=now,
                )
            )

        page = client.get("/api/v1/events", params={"after_id": 0, "limit": 100})
        assert page.status_code == 200, page.text
        assert page.json()["items"] == [
            {
                "id": page.json()["next_after_id"],
                "event_type": "state",
                "experiment_id": experiment["id"],
                "run_id": run_id,
                "payload": {"status": "RUNNING"},
                "created_at": page.json()["items"][0]["created_at"],
            }
        ]
        tail = client.get("/api/v1/events", params={"tail": True}).json()
        assert tail == {"items": [], "next_after_id": page.json()["next_after_id"]}
