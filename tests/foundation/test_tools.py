from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_builtin_tools_are_independent_versioned_entities(database_url):
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
