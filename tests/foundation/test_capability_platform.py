from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from generative_agents.persistence.models import (
    CapabilityBundle,
    CapabilityBundleRevision,
    CapabilityDefinition,
    CapabilityRevision,
)
from generative_agents.web import create_app


def _contract(
    name: str,
    *,
    kind: str = "ADAPTER",
    targets: list[str] | None = None,
    inputs: list[dict] | None = None,
    outputs: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "ga-capability/v1",
        "name": name,
        "summary": f"{name} test contract",
        "kind": kind,
        "targets": targets or ["INTERACTION"],
        "interfaces": [],
        "parameters_schema": {
            "type": "object",
            "properties": {
                "gain": {"type": "number", "minimum": 0},
            },
            "required": ["gain"],
            "additionalProperties": False,
        },
        "inputs": inputs or [],
        "outputs": outputs or [],
        "state_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "triggers": [
            {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
        ],
        "implementation": {
            "kind": "RULES",
            "source": "return inputs",
            "config": {},
            "deterministic": True,
        },
        "dependencies": [],
        "permissions": [],
        "observability": {
            "record_inputs": True,
            "record_outputs": True,
            "record_state": False,
            "metric_outputs": [],
            "sensitive_inputs": [],
        },
    }


def _create_and_publish_capability(
    client: TestClient, *, key: str, contract: dict
) -> dict:
    created = client.post(
        "/api/v1/capabilities",
        json={"name": contract["name"], "capability_key": key, "contract": contract},
    )
    assert created.status_code == 201, created.text
    capability = created.json()
    draft = client.get(
        f"/api/v1/capabilities/{capability['id']}/draft"
    ).json()
    published = client.post(
        f"/api/v1/capabilities/{capability['id']}/draft/publish",
        json={
            "draft_revision_id": draft["id"],
            "lock_version": draft["lock_version"],
        },
    )
    assert published.status_code == 200, published.text
    return published.json()


def test_builtin_capability_catalog_is_versioned_and_filterable(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/capabilities?page=1&page_size=100")
        assert catalog.status_code == 200, catalog.text
        data = catalog.json()
        assert data["total"] >= 10
        builtins = [item for item in data["items"] if item["is_builtin"]]
        assert len(builtins) == 11
        assert all(item["current_draft"] is None for item in builtins)
        assert all(item["current_published"]["state"] == "PUBLISHED" for item in builtins)
        assert all(
            item["active_contract"]["schema_version"] == "ga-capability/v1"
            for item in builtins
        )

        sensors = client.get("/api/v1/capabilities?kind=SENSOR&page_size=100").json()
        assert sensors["items"]
        assert all(item["active_contract"]["kind"] == "SENSOR" for item in sensors["items"])

        builtin = builtins[0]
        draft = client.get(f"/api/v1/capabilities/{builtin['id']}/draft")
        assert draft.status_code == 409
        assert draft.json()["error"]["code"] == "CAPABILITY_DRAFT_UNAVAILABLE"

        signal = next(
            item
            for item in builtins
            if item["capability_key"] == "traffic-signal-cycle"
        )
        signal_contract = signal["active_contract"]
        assert signal_contract["implementation"]["kind"] == "PYTHON"
        assert signal_contract["implementation"]["deterministic"] is True
        assert signal_contract["permissions"] == [
            "execute-python",
            "read-virtual-time",
        ]
        assert signal_contract["inputs"][0]["data_type"] == "state/zone_presence"
        assert signal_contract["outputs"][0]["data_type"] == "state/signal"


def test_builtin_reseeding_versions_changed_capabilities_and_bundles(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        timer = next(
            item
            for item in client.get("/api/v1/capabilities?page_size=100").json()[
                "items"
            ]
            if item["capability_key"] == "core-timer"
        )
        vehicle = next(
            item
            for item in client.get("/api/v1/capability-bundles?page_size=100").json()[
                "items"
            ]
            if item["bundle_key"] == "vehicle-yield-behavior"
        )
        old_timer_revision = timer["current_published"]["id"]
        old_vehicle_revision = vehicle["current_published"]["id"]
        with app.state.database.session_factory.begin() as session:
            timer_model = session.scalar(
                select(CapabilityDefinition).where(
                    CapabilityDefinition.capability_key == "core-timer"
                )
            )
            timer_revision = session.get(
                CapabilityRevision, timer_model.current_published_revision_id
            )
            timer_revision.contract_hash = "previous-builtin-contract"
            vehicle_model = session.scalar(
                select(CapabilityBundle).where(
                    CapabilityBundle.bundle_key == "vehicle-yield-behavior"
                )
            )
            vehicle_revision = session.get(
                CapabilityBundleRevision,
                vehicle_model.current_published_revision_id,
            )
            vehicle_revision.composition_hash = "previous-builtin-composition"

        app.state.capability_service.ensure_builtin_capabilities()
        app.state.capability_service.ensure_builtin_bundles()
        updated_timer = client.get(f"/api/v1/capabilities/{timer['id']}").json()
        updated_vehicle = client.get(
            f"/api/v1/capability-bundles/{vehicle['id']}"
        ).json()
        assert updated_timer["current_published"]["id"] != old_timer_revision
        assert updated_timer["current_published"]["revision_no"] == 2
        assert updated_vehicle["current_published"]["id"] != old_vehicle_revision
        assert updated_vehicle["current_published"]["revision_no"] == 2
        updated_timer_revision = client.get(
            f"/api/v1/capabilities/{timer['id']}/revisions/"
            f"{updated_timer['current_published']['id']}"
        ).json()
        updated_vehicle_revision = client.get(
            f"/api/v1/capability-bundles/{vehicle['id']}/revisions/"
            f"{updated_vehicle['current_published']['id']}"
        ).json()
        assert updated_timer_revision["base_revision_id"] == old_timer_revision
        assert updated_vehicle_revision["base_revision_id"] == old_vehicle_revision

        current_timer_revision = updated_timer["current_published"]["id"]
        current_vehicle_revision = updated_vehicle["current_published"]["id"]
        app.state.capability_service.ensure_builtin_capabilities()
        app.state.capability_service.ensure_builtin_bundles()
        assert client.get(f"/api/v1/capabilities/{timer['id']}").json()[
            "current_published"
        ]["id"] == current_timer_revision
        assert client.get(f"/api/v1/capability-bundles/{vehicle['id']}").json()[
            "current_published"
        ]["id"] == current_vehicle_revision


def test_capability_draft_publish_conflict_and_fork_lifecycle(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        contract = _contract(
            "Passage intention",
            kind="DECISION",
            targets=["BRAIN", "INTERACTION"],
            outputs=[
                {
                    "key": "action",
                    "name": "Action",
                    "data_type": "command/passage_action",
                }
            ],
        )
        created = client.post(
            "/api/v1/capabilities",
            json={
                "name": "Passage intention",
                "capability_key": "passage-intention-test",
                "contract": contract,
            },
        )
        assert created.status_code == 201, created.text
        capability = created.json()
        draft = client.get(
            f"/api/v1/capabilities/{capability['id']}/draft"
        ).json()

        edited = deepcopy(draft["contract"])
        edited["summary"] = "Versioned decision policy"
        saved = client.put(
            f"/api/v1/capabilities/{capability['id']}/draft",
            json={"lock_version": draft["lock_version"], "contract": edited},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["lock_version"] == 2

        stale = client.put(
            f"/api/v1/capabilities/{capability['id']}/draft",
            json={"lock_version": draft["lock_version"], "contract": edited},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "CAPABILITY_REVISION_CONFLICT"

        published = client.post(
            f"/api/v1/capabilities/{capability['id']}/draft/publish",
            json={"draft_revision_id": draft["id"], "lock_version": 2},
        )
        assert published.status_code == 200, published.text
        revision = published.json()
        assert revision["readonly"] is True
        assert revision["validation"]["valid"] is True

        forked = client.post(
            f"/api/v1/capabilities/{capability['id']}/revisions/{revision['id']}/fork"
        )
        assert forked.status_code == 201, forked.text
        assert forked.json()["state"] == "DRAFT"
        assert forked.json()["base_revision_id"] == revision["id"]

        original = client.get(
            f"/api/v1/capabilities/{capability['id']}/revisions/{revision['id']}"
        ).json()
        assert original["contract"]["summary"] == "Versioned decision policy"
        assert original["readonly"] is True


def test_python_capability_requires_explicit_execution_permission(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        contract = _contract("Unsafe Python")
        contract["implementation"] = {
            "kind": "PYTHON",
            "source": "def run(inputs, state, parameters):\n    return {}",
            "config": {},
            "deterministic": True,
        }
        response = client.post(
            "/api/v1/capabilities",
            json={
                "name": "Unsafe Python",
                "capability_key": "unsafe-python-test",
                "contract": contract,
            },
        )
        assert response.status_code == 422
        errors = response.json()["error"]["details"]["errors"]
        assert any("execute-python" in item["msg"] for item in errors)


def test_capability_bundle_validates_parameters_ports_and_versions(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        producer = _create_and_publish_capability(
            client,
            key="motion-producer-test",
            contract=_contract(
                "Motion producer",
                outputs=[
                    {
                        "key": "motion",
                        "name": "Motion",
                        "data_type": "state/motion",
                    }
                ],
            ),
        )
        consumer = _create_and_publish_capability(
            client,
            key="motion-consumer-test",
            contract=_contract(
                "Motion consumer",
                inputs=[
                    {
                        "key": "motion",
                        "name": "Motion",
                        "data_type": "state/motion",
                        "required": True,
                    }
                ],
            ),
        )
        composition = {
            "schema_version": "ga-capability-bundle/v1",
            "name": "Typed motion pipeline",
            "summary": "Reusable typed capability composition",
            "targets": ["INTERACTION"],
            "instances": [
                {
                    "instance_key": "producer",
                    "capability_revision_id": producer["id"],
                    "target_ref": "interaction:subject",
                    "parameters": {"gain": 1.0},
                    "run_policy": {
                        "trigger": "FIXED_INTERVAL",
                        "interval_ms": 200,
                        "event_types": [],
                    },
                    "enabled": True,
                },
                {
                    "instance_key": "consumer",
                    "capability_revision_id": consumer["id"],
                    "target_ref": "interaction:observer",
                    "parameters": {"gain": 1.0},
                    "run_policy": {
                        "trigger": "FIXED_INTERVAL",
                        "interval_ms": 200,
                        "event_types": [],
                    },
                    "enabled": True,
                },
            ],
            "bindings": [
                {
                    "binding_key": "motion_to_consumer",
                    "source": {"instance_key": "producer", "port_key": "motion"},
                    "target": {"instance_key": "consumer", "port_key": "motion"},
                    "delivery": "LATEST",
                }
            ],
            "exposed_parameters_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
        created = client.post(
            "/api/v1/capability-bundles",
            json={
                "name": "Typed motion pipeline",
                "bundle_key": "typed-motion-pipeline-test",
                "composition": composition,
            },
        )
        assert created.status_code == 201, created.text
        bundle = created.json()
        draft = client.get(
            f"/api/v1/capability-bundles/{bundle['id']}/draft"
        ).json()
        published = client.post(
            f"/api/v1/capability-bundles/{bundle['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": draft["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["validation"]["valid"] is True

        invalid = deepcopy(composition)
        invalid["name"] = "Missing parameter"
        invalid["instances"][1]["parameters"] = {}
        bad = client.post(
            "/api/v1/capability-bundles",
            json={
                "name": "Missing parameter",
                "bundle_key": "missing-parameter-test",
                "composition": invalid,
            },
        )
        assert bad.status_code == 201, bad.text
        bad_bundle = bad.json()
        bad_draft = client.get(
            f"/api/v1/capability-bundles/{bad_bundle['id']}/draft"
        ).json()
        rejected = client.post(
            f"/api/v1/capability-bundles/{bad_bundle['id']}/draft/publish",
            json={
                "draft_revision_id": bad_draft["id"],
                "lock_version": bad_draft["lock_version"],
            },
        )
        assert rejected.status_code == 422
        error_codes = {
            item["code"]
            for item in rejected.json()["error"]["details"]["errors"]
        }
        assert "CAPABILITY_PARAMETERS_INVALID" in error_codes


def test_capability_routes_are_exposed_in_openapi(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/capabilities" in paths
        assert "/api/v1/capabilities/{capability_id}/draft/publish" in paths
        assert "/api/v1/capability-bundles" in paths
        assert "/api/v1/capability-bundles/{bundle_id}/draft/publish" in paths


def test_capability_workspace_exposes_form_driven_editors():
    static = Path(__file__).parents[2] / "generative_agents" / "web" / "static"
    html = (static / "experiment-console.html").read_text(encoding="utf-8")
    javascript = (static / "capability-workspace.js").read_text(encoding="utf-8")

    assert 'data-page="capabilities"' in html
    assert 'id="page-capabilities"' in html
    assert 'id="atomicCapabilityEditor"' in html
    assert 'id="capabilityBundleEditor"' in html
    assert 'id="capabilityInputList"' in html
    assert 'id="capabilityOutputList"' in html
    assert 'id="bundleInstanceList"' in html
    assert 'id="bundleBindingList"' in html
    assert "buildContract()" in javascript
    assert "localBundleErrors" in javascript
    assert "defaultParameters(contract.parameters_schema)" in javascript
