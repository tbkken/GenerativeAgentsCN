from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web.app import create_app


ROOT = Path(__file__).resolve().parents[2]


def _agent_definition(name: str, key: str) -> dict:
    return {
        "agent_key": key,
        "enabled": True,
        "name": name,
        "portrait_asset": None,
        "sprite_asset": None,
        "model_override": None,
        "tags": ["测试"],
        "goals": ["验证人群组合"],
        "coord": [36, 65],
        "currently": "参与测试",
        "scratch": {
            "age": 30,
            "innate": "严谨",
            "learned": "熟悉社区",
            "lifestyle": "规律",
            "daily_plan": "参与实验",
        },
        "spatial": {
            "address": {
                "living_area": ["the Ville", "摩尔家族的房子", "主人房"]
            },
            "tree": {
                "the Ville": {
                    "摩尔家族的房子": {
                        "主人房": ["床", "书桌"]
                    }
                }
            },
        },
    }


def _publish_agent(client: TestClient, name: str, key: str) -> tuple[dict, dict]:
    created = client.post(
        "/api/v1/agent-templates",
        json={"definition": _agent_definition(name, key), "description": "用户自定义 Agent"},
    )
    assert created.status_code == 201, created.text
    draft = client.get(f"/api/v1/agent-templates/{created.json()['id']}/draft").json()
    published = client.post(
        f"/api/v1/agent-templates/{created.json()['id']}/draft/publish",
        json={"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )
    assert published.status_code == 200, published.text
    return created.json(), published.json()


def _publish_crowd(
    client: TestClient, name: str, key: str, agent_revision_ids: list[str]
) -> tuple[dict, dict]:
    created = client.post(
        "/api/v1/crowds",
        json={
            "name": name,
            "crowd_key": key,
            "description": "测试人群",
            "agent_revision_ids": agent_revision_ids,
        },
    )
    assert created.status_code == 201, created.text
    draft = client.get(f"/api/v1/crowds/{created.json()['id']}/draft").json()
    published = client.post(
        f"/api/v1/crowds/{created.json()['id']}/draft/publish",
        json={"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )
    assert published.status_code == 200, published.text
    return created.json(), published.json()


def test_builtin_public_agents_and_crowd_are_seeded(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        agents = client.get("/api/v1/agent-templates?page_size=500").json()
        crowds = client.get("/api/v1/crowds?page_size=100").json()

        assert agents["total"] == 25
        assert len({item["name"].strip().casefold() for item in agents["items"]}) == 25
        assert all(item["is_builtin"] and item["current_published"] for item in agents["items"])
        builtin_agent = agents["items"][0]
        builtin_revision = client.get(
            f"/api/v1/agent-templates/{builtin_agent['id']}/revisions/{builtin_agent['current_published']['id']}"
        ).json()
        assert len(builtin_revision["definition"]["coord"]) == 2
        assert builtin_revision["definition"]["spatial"]["address"]["living_area"]
        assert builtin_revision["definition"]["spatial"]["tree"]
        builtin = next(item for item in crowds["items"] if item["is_builtin"])
        assert builtin["agent_count"] == 25
        assert builtin["current_published"]["state"] == "PUBLISHED"


def test_agent_name_is_globally_unique_after_normalization(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        _publish_agent(client, "社区观察员", "community-observer")
        conflict_definition = _agent_definition("  社区观察员  ", "observer-copy")
        conflict = client.post(
            "/api/v1/agent-templates",
            json={"definition": conflict_definition, "description": "重名"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "AGENT_NAME_CONFLICT"


def test_multiple_crowds_dedupe_by_agent_name_and_isolate_experiment_copy(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        system_agents = client.get("/api/v1/agent-templates?page_size=500").json()["items"]
        first_system_revision = system_agents[0]["current_published"]["id"]
        second_system_revision = system_agents[1]["current_published"]["id"]
        custom, custom_revision = _publish_agent(client, "社区记录员", "community-recorder")

        _, crowd_a = _publish_crowd(
            client,
            "观察组 A",
            "observer-group-a",
            [first_system_revision, custom_revision["id"]],
        )
        _, crowd_b = _publish_crowd(
            client,
            "观察组 B",
            "observer-group-b",
            [first_system_revision, second_system_revision],
        )

        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "多个人群去重实验",
                "crowd_revision_ids": [crowd_a["id"], crowd_b["id"]],
            },
        )
        assert created.status_code == 201, created.text
        draft = client.get(f"/api/v1/experiments/{created.json()['id']}/draft").json()
        imported = draft["definition"]["agents"]
        assert len(imported) == 3
        assert len({item["name"].strip().casefold() for item in imported}) == 3
        assert len({tuple(item["coord"]) for item in imported}) == 3
        assert draft["provenance"]["crowd_agent_input_count"] == 4
        assert draft["provenance"]["crowd_agent_count"] == 3
        assert len(draft["provenance"]["crowd_agent_duplicate_names"]) == 1

        custom_copy = next(item for item in imported if item["name"] == "社区记录员")
        assert custom_copy["coord"] == [36, 65]
        assert custom_copy["spatial"] == custom_revision["definition"]["spatial"]
        custom_copy["currently"] = "只修改实验副本"
        updated = client.put(
            f"/api/v1/experiments/{created.json()['id']}/draft/agents/{custom_copy['agent_key']}",
            json={"lock_version": draft["lock_version"], "data": custom_copy},
        )
        assert updated.status_code == 200, updated.text
        public_revision = client.get(
            f"/api/v1/agent-templates/{custom['id']}/revisions/{custom_revision['id']}"
        ).json()
        assert public_revision["definition"]["currently"] == "参与测试"
        assert public_revision["definition"]["coord"] == [36, 65]
        assert public_revision["definition"]["spatial"]["address"]["living_area"]


def test_agent_template_publish_rejects_missing_spatial_configuration(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        definition = _agent_definition("缺少空间的 Agent", "missing-spatial")
        definition["spatial"] = {"address": {}, "tree": {}}
        created = client.post(
            "/api/v1/agent-templates",
            json={"definition": definition, "description": "应被发布校验拦截"},
        )
        assert created.status_code == 201
        draft = client.get(
            f"/api/v1/agent-templates/{created.json()['id']}/draft"
        ).json()
        published = client.post(
            f"/api/v1/agent-templates/{created.json()['id']}/draft/publish",
            json={"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
        )
        assert published.status_code == 422
        assert published.json()["error"]["code"] == "AGENT_SPATIAL_ADDRESS_REQUIRED"


def test_crowd_workspace_and_create_flow_replace_presets():
    html = (ROOT / "generative_agents/web/static/experiment-console.html").read_text(encoding="utf-8")
    console_js = (ROOT / "generative_agents/web/static/console-api.js").read_text(encoding="utf-8")
    crowd_js = (ROOT / "generative_agents/web/static/crowd-workspace.js").read_text(encoding="utf-8")
    crowd_css = (ROOT / "generative_agents/web/static/crowd-workspace.css").read_text(encoding="utf-8")

    assert 'data-page="crowds"' in html
    assert 'id="newExperimentCrowds"' in html
    assert 'id="crowdAgentManagerModal"' in html
    assert 'class="crowd-agent-registry"' in html
    assert html.count('id="agentEditorModal"') == 1
    assert 'id="publicAgentEditorModal"' not in html
    assert 'id="quickExperienceBtn"' not in html
    assert "agent_preset" not in console_js
    assert "crowd_revision_ids" in console_js
    assert "selectedCreateRevisionIds" in crowd_js
    assert "SharedAgentEditor.openPublic" in crowd_js
    assert "saveSharedAgent" in crowd_js
    assert "loadAgentRevisionDetails" in crowd_js
    assert "agentCardMarkup" in crowd_js
    assert "selectedRevisionIdForAgent" in crowd_js
    assert "clearAgentSelection" in crowd_js
    assert "人群锁定 v" in crowd_js
    assert "升级到 v" in crowd_js
    assert "const selected = [...this.memberSelection]" in crowd_js
    assert "完整 Agent 定义 JSON" not in crowd_js
    assert 'class="content-workspace crowd-agent-readonly-workspace"' in crowd_js
    assert 'data-agent-card-tab="identity"' in crowd_js
    assert 'data-agent-card-tab="traits"' in crowd_js
    assert 'data-agent-card-tab="space"' in crowd_js
    assert 'class="agent-image-editor crowd-agent-readonly-images"' in crowd_js
    assert 'spatial-form-editor crowd-agent-readonly-spatial' in crowd_js
    assert 'crowd-agent-fields' not in crowd_js
    assert '<h2>Agent</h2>' in html
    assert '<h2>Agent 成员</h2>' not in html
    assert 'data-view-crowd-agent=' in crowd_js
    assert "SharedAgentEditor.openReadOnly" in crowd_js
    assert "ownerType: 'public-readonly'" in console_js
    assert "setAgentEditorReadOnly(true)" in console_js
    assert "openReadOnly: openPublicAgentReadOnly" in console_js
    assert "document.querySelector('[data-content-tab=\"space\"]').hidden = false" in console_js
    assert "coord: [Number($('agentEditX').value), Number($('agentEditY').value)]" in console_js
    assert "spatial," in console_js
    assert "width:calc(100vw - 24px); height:calc(100vh - 24px); max-height:none" in crowd_css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in crowd_css
    assert "/agent-templates" in crowd_js
    assert "/crowds" in crowd_js
