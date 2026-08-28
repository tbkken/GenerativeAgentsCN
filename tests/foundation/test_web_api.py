"""基础能力回归测试：覆盖 ``test_web_api`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from datetime import datetime, timezone
import struct
import zlib

from fastapi.testclient import TestClient

from generative_agents.persistence.models import ExperimentRevision, Run, RunEvent
from generative_agents.web import create_app
from tests.support import first_builtin_crowd_revision_id, publish_user_map_via_api


def _test_png(width: int, height: int) -> bytes:
    """为本测试模块封装 ``_test_png`` 辅助步骤，减少重复的场景搭建代码。"""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        """为本测试模块封装 ``chunk`` 辅助步骤，减少重复的场景搭建代码。"""
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\x00" + b"\x2c\x91\x76\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class _ModelResponse:
    """为 ``_ModelResponse`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self, body, status_code=200):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        """为本测试模块封装 ``json`` 辅助步骤，减少重复的场景搭建代码。"""
        return self._body


class _AutoModelSession:
    """为 ``_AutoModelSession`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls = []

    def get(self, url, **kwargs):
        """为本测试模块封装 ``get`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls.append(("GET", url))
        model_id = "test-embedding" if ":5002/" in url else "test-chat"
        return _ModelResponse(
            {"data": [{"id": model_id, "max_model_len": 40_000}]}
        )

    def post(self, url, **kwargs):
        """为本测试模块封装 ``post`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls.append(("POST", url))
        if url.endswith("/embeddings"):
            return _ModelResponse({"data": [{"embedding": [0.1, 0.2]}]})
        return _ModelResponse({"choices": [{"message": {"content": "OK"}}]})


class _OfflineModelSession:
    def get(self, _url, **_kwargs):
        return _ModelResponse({"detail": "unauthorized"}, status_code=401)

    def post(self, _url, **_kwargs):
        return _ModelResponse({"detail": "unauthorized"}, status_code=401)


def test_experiment_api_create_list_validate_and_conflict(database_url):
    """回归验证 ``test_experiment_api_create_list_validate_and_conflict`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url)
    with TestClient(app) as client:
        map_revision = publish_user_map_via_api(client)
        created_response = client.post(
            "/api/v1/experiments",
            json={
                "name": "API Experiment",
                "goal": "Exercise the real service",
                "brain_skill": "stanford-town-brain",
                "source": {"type": "BLANK"},
                "map_revision_id": map_revision["id"],
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


def test_publish_and_run_resolves_auto_models_without_manual_probe(database_url):
    """回归验证 ``test_publish_and_run_resolves_auto_models_without_manual_probe`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        session = _AutoModelSession()
        app.state.model_probe_service._session = session
        map_revision = publish_user_map_via_api(client)
        crowd_revision_id = first_builtin_crowd_revision_id(client)
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Auto model run",
                "brain_skill": "stanford-town-brain",
                "map_revision_id": map_revision["id"],
                "crowd_revision_ids": [crowd_revision_id],
            },
        ).json()
        draft = client.get(f"/api/v1/experiments/{created['id']}/draft").json()
        models = draft["definition"]["models"]
        models["chat"]["model"] = "auto"
        models["chat"]["resolved_model"] = None
        models["chat"]["context_window"] = None
        forced_auto = client.patch(
            f"/api/v1/experiments/{created['id']}/draft/models",
            json={"lock_version": draft["lock_version"], "data": models},
        )
        assert forced_auto.status_code == 200, forced_auto.text
        draft = forced_auto.json()

        response = client.post(
            f"/api/v1/experiments/{created['id']}/actions/publish-and-run",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": draft["lock_version"],
            },
        )

        assert response.status_code == 202, response.text
        run = response.json()
        revision = client.get(
            f"/api/v1/experiments/{created['id']}/revisions/{run['revision_id']}"
        ).json()
        assert revision["definition"]["models"]["chat"]["resolved_model"] == "test-chat"
        assert revision["definition"]["models"]["chat"]["context_window"] == 40_000
        assert revision["definition"]["models"]["embedding"]["resolved_model"] == "test-embedding"
        assert [method for method, _url in session.calls] == [
            "GET",
            "POST",
            "GET",
            "POST",
        ]


def test_api_errors_have_uniform_envelope_and_request_id(database_url):
    """回归验证 ``test_api_errors_have_uniform_envelope_and_request_id`` 所描述的业务结果、故障边界和隔离约束。"""
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


def test_offline_model_probe_is_counted_once_as_a_publish_warning(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        map_revision = publish_user_map_via_api(client)
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Offline model warning",
                "brain_skill": "stanford-town-brain",
                "source": {"type": "BLANK"},
                "map_revision_id": map_revision["id"],
            },
        ).json()
        draft = client.get(f"/api/v1/experiments/{created['id']}/draft").json()
        app.state.model_probe_service._session = _OfflineModelSession()
        failed = client.post(
            f"/api/v1/experiments/{created['id']}/draft/models/chat/test",
            json={"lock_version": draft["lock_version"]},
        )
        assert failed.status_code >= 400

        report = client.post(
            f"/api/v1/experiments/{created['id']}/draft/validate"
        ).json()

    model_warnings = [
        item for item in report["warnings"] if item["path"] == "models.chat"
    ]
    assert len(model_warnings) == 1
    assert model_warnings[0]["code"] == "MODEL_ENDPOINT_ERROR"
    assert report["counts"]["warning"] == len(report["warnings"])
    assert report["counts"]["automatic"] == 1


def test_metadata_agent_prompt_and_world_draft_routes_are_optimistic(database_url):
    """回归验证 ``test_metadata_agent_prompt_and_world_draft_routes_are_optimistic`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        map_revision = publish_user_map_via_api(client)
        crowd_revision_id = first_builtin_crowd_revision_id(client)
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Editable",
                "brain_skill": "stanford-town-brain",
                "map_revision_id": map_revision["id"],
                "crowd_revision_ids": [crowd_revision_id],
            },
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

        removed_prompt_route = client.put(
            f"/api/v1/experiments/{created['id']}/draft/prompts/base_desc",
            json={"lock_version": patched["lock_version"], "data": {"content": "独立正文"}},
        )
        assert removed_prompt_route.status_code == 404
        base_desc = client.get("/api/v1/skills/base-desc")
        assert base_desc.status_code == 200
        assert base_desc.json()["kind"] == "atomic"

        world = patched["definition"]["world"]
        world["world_name"] = "Owned world"
        saved_world = client.put(
            f"/api/v1/experiments/{created['id']}/draft/world",
            json={"lock_version": patched["lock_version"], "data": world},
        )
        assert saved_world.status_code == 200
        assert saved_world.json()["definition"]["world"]["world_name"] == "Owned world"


def test_duplicate_experiment_deep_copies_the_selected_definition(database_url):
    """回归验证 ``test_duplicate_experiment_deep_copies_the_selected_definition`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        map_revision = publish_user_map_via_api(client)
        source = client.post(
            "/api/v1/experiments",
            json={
                "name": "Source",
                "brain_skill": "stanford-town-brain",
                "source": {"type": "BLANK"},
                "map_revision_id": map_revision["id"],
            },
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
    """回归验证 ``test_asset_and_secret_http_contracts_are_safe_and_idempotent`` 所描述的业务结果、故障边界和隔离约束。"""
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

        agent_images = client.post(
            "/api/v1/agent-images",
            files={
                "portrait": ("portrait.png", _test_png(64, 64), "image/png"),
                "sprite": ("sprite.png", _test_png(128, 128), "image/png"),
            },
        )
        assert agent_images.status_code == 201, agent_images.text
        image_payload = agent_images.json()
        assert image_payload["sprite"]["content_url"].startswith("/api/v1/agent-images/")
        sprite_content = client.get(image_payload["sprite"]["content_url"])
        assert sprite_content.content == _test_png(128, 128)
        assert sprite_content.headers["content-type"] == "image/png"
        assert "immutable" in sprite_content.headers["cache-control"]

        invalid_sprite = client.post(
            "/api/v1/agent-images",
            files={"sprite": ("sprite.png", _test_png(96, 128), "image/png")},
        )
        assert invalid_sprite.status_code == 422
        assert invalid_sprite.json()["error"]["code"] == "INVALID_AGENT_SPRITE_SIZE"

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
    """回归验证 ``test_health_endpoint_checks_database_connectivity`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_global_event_cursor_exposes_run_activity_with_experiment_identity(database_url):
    """回归验证 ``test_global_event_cursor_exposes_run_activity_with_experiment_identity`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        map_revision = publish_user_map_via_api(client)
        experiment = client.post(
            "/api/v1/experiments",
            json={
                "name": "Live status",
                "brain_skill": "stanford-town-brain",
                "source": {"type": "BLANK"},
                "map_revision_id": map_revision["id"],
            },
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
