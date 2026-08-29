"""基础能力回归测试：覆盖 ``test_product_usability_requirements`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web.app import create_app
from tests.support import brain_revision_via_api, publish_user_map_via_api


ROOT = Path(__file__).resolve().parents[2]


def _create(client: TestClient, name: str, source_type: str = "CROWD", **metadata):
    """为本测试模块封装 ``_create`` 辅助步骤，减少重复的场景搭建代码。"""
    source = {"type": source_type} if source_type == "BLANK" else None
    crowd_revision_ids = []
    if source is None:
        crowd = next(
            item for item in client.get("/api/v1/crowds?page_size=100").json()["items"]
            if item["is_builtin"]
        )
        crowd_revision_ids = [crowd["current_published"]["id"]]
    map_revision = publish_user_map_via_api(client, name=f"{name} map")
    brain = brain_revision_via_api(
        client, metadata.get("brain_skill", "stanford-town-brain")
    )
    response = client.post(
        "/api/v1/experiments",
        json={
            "name": name,
            "goal": "产品可用性验收",
            "owner": metadata.get("owner", "产品研究员"),
            "tags": metadata.get("tags", ["UX验收"]),
            "brain_skill": brain["name"],
            "brain_revision_id": brain["revision_id"],
            "source": source,
            "map_revision_id": map_revision["id"],
            "crowd_revision_ids": crowd_revision_ids,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_crowd_based_creation_metadata_filters_views_and_lifecycle(database_url):
    """回归验证 ``test_crowd_based_creation_metadata_filters_views_and_lifecycle`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        first = _create(client, "人群实验 A")
        second = _create(client, "人群实验 B", owner="另一位研究员")

        first_draft = client.get(f"/api/v1/experiments/{first['id']}/draft").json()
        second_draft = client.get(f"/api/v1/experiments/{second['id']}/draft").json()
        assert len(first_draft["definition"]["agents"]) == 25
        assert len(second_draft["definition"]["agents"]) == 25

        by_owner = client.get("/api/v1/experiments", params={"owner": "产品研究员", "page_size": 5}).json()
        by_tag = client.get("/api/v1/experiments", params={"tag": "UX验收", "page_size": 5}).json()
        tag_search = client.get("/api/v1/experiments", params={"q": "UX验收", "page_size": 5}).json()
        assert [item["id"] for item in by_owner["items"]] == [first["id"]]
        assert {item["id"] for item in by_tag["items"]} == {first["id"], second["id"]}
        assert {item["id"] for item in tag_search["items"]} == {first["id"], second["id"]}

        saved = client.post(
            "/api/v1/experiment-saved-views",
            json={"name": "UX验收视图", "query": {"tag": "UX验收", "sort": "-updated_at", "page_size": 5}},
        )
        assert saved.status_code == 201
        shared = client.get(f"/api/v1/experiment-saved-views/shared/{saved.json()['share_key']}")
        assert shared.json()["query"]["page_size"] == 5

        batch = client.post(
            "/api/v1/experiments/batch",
            json={"experiment_ids": [first["id"], second["id"]], "action": "ADD_TAGS", "tags": ["批次一"]},
        )
        assert batch.json()["affected"] == 2
        archived = client.post(f"/api/v1/experiments/{first['id']}/archive", json={})
        assert archived.status_code == 200
        assert client.get("/api/v1/experiments", params={"archived": "active"}).json()["total"] == 1
        restored = client.post(f"/api/v1/experiments/{first['id']}/restore", json={})
        assert restored.status_code == 200


def test_resource_first_creation_selects_skill_brain_map_and_multiple_crowds(database_url):
    """回归验证 ``test_resource_first_creation_selects_skill_brain_map_and_multiple_crowds`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        brain = client.get("/api/v1/skills/pedestrian-crossing-brain")
        assert brain.status_code == 200
        assert brain.json()["kind"] == "brain"
        public_map = publish_user_map_via_api(client, name="Resource selected map")
        crowd = next(
            item for item in client.get("/api/v1/crowds?page_size=100").json()["items"]
            if item["is_builtin"]
        )
        response = client.post(
            "/api/v1/experiments",
            json={
                "name": "资源优先创建",
                "goal": "验证创建入口组合大脑、地图和一个或多个人群",
                "brain_skill": "pedestrian-crossing-brain",
                "brain_revision_id": brain.json()["revision_id"],
                "map_revision_id": public_map["id"],
                "crowd_revision_ids": [crowd["current_published"]["id"]],
            },
        )
        assert response.status_code == 201, response.text
        draft = client.get(
            f"/api/v1/experiments/{response.json()['id']}/draft"
        ).json()
        assert len(draft["definition"]["agents"]) == 25
        assert draft["definition"]["engine"]["brain_skill"] == "pedestrian-crossing-brain"
        assert draft["definition"]["world"]["map_revision_id"] == public_map["id"]
        assert draft["definition"]["engine"]["brain_revision_id"] == brain.json()["revision_id"]
        assert draft["definition"]["engine"]["brain_revision_hash"] == brain.json()["revision"]
        assert draft["provenance"]["brain_revision_id"] == brain.json()["revision_id"]
        assert draft["provenance"]["world_map_revision_id"] == public_map["id"]
        assert draft["provenance"]["world_map_revision_hash"] == public_map["world_hash"]
        assert draft["provenance"]["crowd_revision_ids"] == [crowd["current_published"]["id"]]

        missing_crowd = client.post(
            "/api/v1/experiments",
            json={
                "name": "缺少人群",
                "brain_skill": "stanford-town-brain",
                "brain_revision_id": brain_revision_via_api(client)["revision_id"],
                "map_revision_id": public_map["id"],
            },
        )
        assert missing_crowd.status_code == 422
        assert missing_crowd.json()["error"]["code"] == "CROWD_REQUIRED"

        atomic = client.get("/api/v1/skills/wake-up").json()
        wrong_kind = client.post(
            "/api/v1/experiments",
            json={
                "name": "错误大脑类型",
                "brain_skill": "wake-up",
                "brain_revision_id": atomic["revision_id"],
                "map_revision_id": public_map["id"],
                "crowd_revision_ids": [crowd["current_published"]["id"]],
            },
        )
        assert wrong_kind.status_code == 422
        assert wrong_kind.json()["error"]["code"] == "BRAIN_SKILL_KIND_REQUIRED"


def test_blank_publish_is_blocked_and_blank_map_is_fully_initialized(database_url):
    """回归验证 ``test_blank_publish_is_blocked_and_blank_map_is_fully_initialized`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        blank = _create(client, "空白实验", "BLANK")
        report = client.post(f"/api/v1/experiments/{blank['id']}/draft/validate").json()
        codes = {item["code"] for item in report["errors"]}
        assert report["valid"] is False
        assert report["counts"]["blocking"] >= 1
        assert "NO_ENABLED_AGENT" in codes
        assert "WORLD_EMPTY" not in codes
        assert "MODEL_NOT_RESOLVED" not in codes
        assert "MODEL_SERVICE_NOT_ONLINE" not in codes
        assert report["counts"]["automatic"] == 2
        assert report["auto_model_probe"] == {
            "enabled": True,
            "purposes": ["chat", "embedding"],
            "count": 2,
        }
        assert report["model_status"]["auto_probe_on_publish"] is True
        assert all(item.get("fix_page") for item in report["errors"])

        created = client.post(
            "/api/v1/maps",
            json={"name": "原子空白地图", "width": 7, "height": 6, "tile_size": 24},
        )
        assert created.status_code == 201, created.text
        draft = client.get(f"/api/v1/maps/{created.json()['id']}/draft").json()
        world = draft["world"]
        assert world["definition"]["size"] == [6, 7]
        assert world["definition"]["tile_size"] == 24
        assert len(world["definition"]["tiles"]) == 42
        assert all(isinstance(tile["collision"], bool) for tile in world["definition"]["tiles"])


def test_agent_batch_estimate_compare_and_persisted_model_state(database_url):
    """回归验证 ``test_agent_batch_estimate_compare_and_persisted_model_state`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        first = _create(client, "Agent 批量 A")
        second = _create(client, "Agent 批量 B")
        draft = client.get(f"/api/v1/experiments/{first['id']}/draft").json()
        keys = [agent["agent_key"] for agent in draft["definition"]["agents"]]
        request = {
            "lock_version": draft["lock_version"],
            "agent_keys": keys,
            "changes": {"enabled": False, "coord": [1, 1], "append_goal": "观察公共事件", "add_tags": ["对照"]},
            "dry_run": True,
        }
        preview = client.post(f"/api/v1/experiments/{first['id']}/draft/agents/batch", json=request)
        assert preview.status_code == 200
        assert preview.json()["affected"] == 25
        assert preview.json()["dry_run"] is True

        request["dry_run"] = False
        applied = client.post(f"/api/v1/experiments/{first['id']}/draft/agents/batch", json=request)
        assert applied.status_code == 200, applied.text
        assert all(not item["enabled"] for item in applied.json()["draft"]["definition"]["agents"])

        estimate = client.get(f"/api/v1/experiments/{second['id']}/run-estimate").json()
        assert estimate["scale"] == {
            "execution_mode": "SKILL_BRAIN",
            "brain_skill": "stanford-town-brain",
            "agents": 25,
            "steps": 1000,
            "virtual_minutes": 10000,
            "projection_interval_steps": 1,
            "capture_model_payloads": False,
        }
        assert estimate["estimate"]["model_calls"]["high"] >= estimate["estimate"]["model_calls"]["low"]
        assert estimate["estimate_version"] == 2
        assert estimate["estimate"]["model_calls"] == {"low": 50001, "high": 150003}

        comparison = client.post(
            "/api/v1/experiments/compare",
            json={"experiment_ids": [first["id"], second["id"]]},
        )
        assert comparison.status_code == 200
        assert comparison.json()["difference_count"] > 0
        assert any(group["key"] == "agents" for group in comparison.json()["groups"])

        statuses = client.get(f"/api/v1/experiments/{second['id']}/draft/models/status").json()
        assert statuses["counts"]["UNTESTED"] == 2
        assert statuses["publish_ready"] is False


def test_agent_deletion_is_list_scoped_and_spatial_data_uses_form_tables():
    """回归验证 ``test_agent_deletion_is_list_scoped_and_spatial_data_uses_form_tables`` 所描述的业务结果、故障边界和隔离约束。"""
    html = (ROOT / "generative_agents/web/static/experiment-console.html").read_text(encoding="utf-8")
    source = (ROOT / "generative_agents/web/static/console-api.js").read_text(encoding="utf-8")

    editor = html[html.index('id="agentEditorModal"') : html.index('id="createMapModal"')]
    assert 'id="deleteAgentBtn"' not in editor
    assert "空间定义 JSON" not in editor
    assert 'id="agentAddressRows"' in editor
    assert 'id="agentSpaceRows"' in editor
    assert '<label for="agentEditKey">稳定键</label>' not in editor
    assert '<input id="agentEditKey" type="hidden"' in editor
    assert 'id="agentEditorKeyMeta">文件键：—' in editor
    assert "画像资源引用" not in editor
    assert 'id="agentPortraitFile"' in editor
    assert 'id="agentSpriteFile"' in editor
    assert "4×4 行走图" in editor
    assert "直接保存为数据库二进制资源" in editor

    assert 'id="deleteSelectedAgentsBtn"' in html
    assert 'id="deleteAgentsModal"' in html
    assert "function openDeleteSelectedAgents" in source
    assert "function deleteSelectedAgents" in source
    assert "function flattenSpatialTree" in source
    assert "function readSpatialEditor" in source
    assert "const spatial = readSpatialEditor();" in source
    assert "function stageAgentImage" in source
    assert "async function uploadStagedAgentImages" in source
    assert "form.append('portrait'" in source and "form.append('sprite'" in source
