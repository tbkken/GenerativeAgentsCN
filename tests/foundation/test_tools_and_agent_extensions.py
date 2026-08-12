from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web import create_app


ROOT = Path(__file__).resolve().parents[2]


def _agent_definition(name: str, key: str) -> dict:
    return {
        "agent_key": key,
        "enabled": True,
        "name": name,
        "portrait_asset": None,
        "sprite_asset": None,
        "model_override": None,
        "tags": ["traffic-test"],
        "goals": ["arrive safely"],
        "coord": [10, 10],
        "currently": "preparing to commute",
        "scratch": {
            "age": 30,
            "innate": "careful",
            "learned": "understands traffic rules",
            "lifestyle": "regular commuter",
            "daily_plan": "go to work",
        },
        "spatial": {
            "address": {
                "living_area": ["test-world", "home", "bedroom"],
                "sleeping": ["test-world", "home", "bedroom", "bed"],
            },
            "tree": {
                "test-world": {"home": {"bedroom": ["bed", "desk"]}}
            },
        },
    }


def _by_key(client: TestClient, endpoint: str, field: str, key: str) -> dict:
    response = client.get(f"/api/v1/{endpoint}?q={key}&page_size=100")
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["items"] if item[field] == key)


def test_builtin_tools_are_non_agent_versioned_entities(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/tools?page_size=100")
        assert response.status_code == 200, response.text
        tools = response.json()["items"]
        assert len([item for item in tools if item["is_builtin"]]) == 4
        assert {item["tool_kind"] for item in tools} == {
            "CAR",
            "BICYCLE",
            "MOTORCYCLE",
            "ACCESS_CARD",
        }
        car = next(item for item in tools if item["tool_key"] == "generic-car")
        assert car["active_contract"]["mobility"]["mode"] == "ROAD"
        assert car["active_contract"]["mobility"]["operator_required"] is True
        assert car["active_contract"]["appearance"]["emoji"] == "🚙"


def test_new_vehicle_tool_gets_a_valid_editable_mobility_default(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tools",
            json={"name": "Delivery car", "tool_kind": "CAR"},
        )
        assert created.status_code == 201, created.text
        contract = created.json()["active_contract"]
        assert contract["kind"] == "CAR"
        assert contract["mobility"]["mode"] == "ROAD"
        assert contract["mobility"]["max_speed_mps"] > 0


def test_agent_can_own_car_and_use_capability_driven_mobility_choice(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        car = _by_key(client, "tools", "tool_key", "generic-car")
        decision_contract = {
            "name": "Commute mode decision",
            "summary": "Choose walking or driving from urgency and availability.",
            "kind": "DECISION",
            "targets": ["AGENT", "BRAIN"],
            "interfaces": ["mobility-choice"],
            "parameters_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": [],
            "outputs": [
                {
                    "key": "mode",
                    "name": "Travel mode",
                    "data_type": "command/travel_mode",
                }
            ],
            "state_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "triggers": [{"mode": "DECISION", "default": True}],
                "implementation": {
                    "kind": "RULES",
                    "config": {
                        "default_outputs": {"mode": {"mode": "FASTEST_AVAILABLE"}}
                    },
                },
        }
        created_decision = client.post(
            "/api/v1/capabilities",
            json={
                "name": "Commute mode decision",
                "capability_key": "commute-mode-decision-test",
                "contract": decision_contract,
            },
        )
        assert created_decision.status_code == 201, created_decision.text
        decision = created_decision.json()
        decision_draft = client.get(
            f"/api/v1/capabilities/{decision['id']}/draft"
        ).json()
        decision_revision = client.post(
            f"/api/v1/capabilities/{decision['id']}/draft/publish",
            json={
                "draft_revision_id": decision_draft["id"],
                "lock_version": decision_draft["lock_version"],
            },
        )
        assert decision_revision.status_code == 200, decision_revision.text

        created_agent = client.post(
            "/api/v1/agent-templates",
            json={
                "definition": _agent_definition("Traffic commuter", "traffic-commuter"),
                "description": "Agent remains a human and owns a car tool.",
            },
        )
        assert created_agent.status_code == 201, created_agent.text
        agent = created_agent.json()
        draft = client.get(f"/api/v1/agent-templates/{agent['id']}/draft").json()
        default_extension = client.get(
            f"/api/v1/agent-templates/{agent['id']}/draft/extension"
        )
        assert default_extension.status_code == 200, default_extension.text
        assert default_extension.json()["is_default"] is True
        original_definition_hash = draft["definition_hash"]

        extension = {
            "schema_version": "ga-agent-extension/v1",
            "capability_bundle_revision_ids": [],
            "tool_grants": [
                {
                    "grant_key": "commuter-car",
                    "tool_revision_id": car["current_published"]["id"],
                    "quantity": 1,
                    "relation": "OWNS",
                    "initial_location_ref": "map:parking-space-01",
                    "available": True,
                    "state_overrides": {"fuel_ratio": 1.0},
                }
            ],
            "mobility_choice": {
                "enabled": True,
                "default_mode": "FASTEST_AVAILABLE",
                "decision_capability_revision_id": decision_revision.json()["id"],
                "decision_bundle_revision_id": None,
                "urgency_threshold_minutes": 15,
                "decision_interval_ms": 60000,
            },
            "reasoning_interval_ms": 60000,
        }
        updated = client.put(
            f"/api/v1/agent-templates/{agent['id']}/draft/extension",
            json={"lock_version": draft["lock_version"], "extension": extension},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["lock_version"] == draft["lock_version"] + 1

        draft_after = client.get(
            f"/api/v1/agent-templates/{agent['id']}/draft"
        ).json()
        assert draft_after["definition_hash"] == original_definition_hash
        published = client.post(
            f"/api/v1/agent-templates/{agent['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": updated.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        published_extension = client.get(
            f"/api/v1/agent-templates/{agent['id']}/revisions/{published.json()['id']}/extension"
        )
        assert published_extension.status_code == 200, published_extension.text
        assert published_extension.json()["readonly"] is True
        assert published_extension.json()["extension"]["tool_grants"][0][
            "relation"
        ] == "OWNS"


def test_agent_extension_rejects_mobility_choice_without_vehicle(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        builtin_decisions = client.get(
            "/api/v1/capabilities?kind=DECISION&page_size=100"
        ).json()["items"]
        if not builtin_decisions:
            # Create a minimal decision capability for the validation path.
            contract = {
                "name": "Mode choice",
                "kind": "DECISION",
                "targets": ["BRAIN"],
                "triggers": [{"mode": "DECISION", "default": True}],
                "implementation": {"kind": "WORKFLOW"},
            }
            created = client.post(
                "/api/v1/capabilities",
                json={
                    "name": "Mode choice",
                    "capability_key": "mode-choice-no-vehicle-test",
                    "contract": contract,
                },
            ).json()
            draft_capability = client.get(
                f"/api/v1/capabilities/{created['id']}/draft"
            ).json()
            revision_id = client.post(
                f"/api/v1/capabilities/{created['id']}/draft/publish",
                json={
                    "draft_revision_id": draft_capability["id"],
                    "lock_version": draft_capability["lock_version"],
                },
            ).json()["id"]
        else:
            revision_id = builtin_decisions[0]["current_published"]["id"]

        created_agent = client.post(
            "/api/v1/agent-templates",
            json={"definition": _agent_definition("Walker only", "walker-only")},
        ).json()
        draft = client.get(
            f"/api/v1/agent-templates/{created_agent['id']}/draft"
        ).json()
        extension = {
            "capability_bundle_revision_ids": [],
            "tool_grants": [],
            "mobility_choice": {
                "enabled": True,
                "default_mode": "FASTEST_AVAILABLE",
                "decision_capability_revision_id": revision_id,
                "decision_bundle_revision_id": None,
                "urgency_threshold_minutes": 5,
                "decision_interval_ms": 1000,
            },
            "reasoning_interval_ms": 60000,
        }
        updated = client.put(
            f"/api/v1/agent-templates/{created_agent['id']}/draft/extension",
            json={"lock_version": draft["lock_version"], "extension": extension},
        )
        assert updated.status_code == 200, updated.text
        rejected = client.post(
            f"/api/v1/agent-templates/{created_agent['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": updated.json()["lock_version"],
            },
        )
        assert rejected.status_code == 422
        codes = {
            item["code"] for item in rejected.json()["error"]["details"]["errors"]
        }
        assert "MOBILITY_CHOICE_HAS_NO_VEHICLE" in codes


def test_agent_extension_is_copied_when_published_revision_is_forked(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        car = _by_key(client, "tools", "tool_key", "generic-car")
        agent = client.post(
            "/api/v1/agent-templates",
            json={"definition": _agent_definition("Fork commuter", "fork-commuter")},
        ).json()
        draft = client.get(f"/api/v1/agent-templates/{agent['id']}/draft").json()
        extension = client.get(
            f"/api/v1/agent-templates/{agent['id']}/draft/extension"
        ).json()["extension"]
        extension["tool_grants"] = [
            {
                "grant_key": "car",
                "tool_revision_id": car["current_published"]["id"],
                "quantity": 1,
                "relation": "OWNS",
                "initial_location_ref": "agent:self",
                "available": True,
                "state_overrides": {},
            }
        ]
        updated = client.put(
            f"/api/v1/agent-templates/{agent['id']}/draft/extension",
            json={"lock_version": draft["lock_version"], "extension": extension},
        ).json()
        published = client.post(
            f"/api/v1/agent-templates/{agent['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": updated["lock_version"],
            },
        ).json()
        forked = client.post(
            f"/api/v1/agent-templates/{agent['id']}/revisions/{published['id']}/fork"
        )
        assert forked.status_code == 201, forked.text
        forked_extension = client.get(
            f"/api/v1/agent-templates/{agent['id']}/draft/extension"
        ).json()
        assert forked_extension["extension"]["tool_grants"][0]["grant_key"] == "car"


def test_stanford_agents_keep_empty_default_extensions(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        agents = client.get("/api/v1/agent-templates?page_size=500").json()["items"]
        builtin = next(item for item in agents if item["is_builtin"])
        extension = client.get(
            f"/api/v1/agent-templates/{builtin['id']}/revisions/{builtin['current_published']['id']}/extension"
        )
        assert extension.status_code == 200, extension.text
        assert extension.json()["is_default"] is True
        assert extension.json()["extension"]["tool_grants"] == []


def test_tool_and_agent_capability_configuration_is_form_driven():
    html = (ROOT / "generative_agents/web/static/experiment-console.html").read_text(
        encoding="utf-8"
    )
    capability_js = (
        ROOT / "generative_agents/web/static/capability-workspace.js"
    ).read_text(encoding="utf-8")
    console_js = (ROOT / "generative_agents/web/static/console-api.js").read_text(
        encoding="utf-8"
    )
    crowd_js = (ROOT / "generative_agents/web/static/crowd-workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'data-capability-asset="tool"' in html
    assert 'id="capabilityToolEditor"' in html
    assert 'id="toolCapabilityAttachmentList"' in html
    assert 'data-content-tab="capabilities"' in html
    assert 'id="agentToolGrantList"' in html
    assert 'id="agentMobilityDecision"' in html
    assert "readToolContract()" in capability_js
    assert "readAgentExtensionEditor()" in console_js
    assert "/draft/extension" in crowd_js
