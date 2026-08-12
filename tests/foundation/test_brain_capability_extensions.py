from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web import create_app


ROOT = Path(__file__).resolve().parents[2]


def _published_bundle(client: TestClient, *, suffix: str, targets: list[str]) -> dict:
    contract = {
        "name": f"Brain capability {suffix}",
        "summary": "Reusable brain behavior for extension tests.",
        "kind": "DECISION",
        "targets": targets,
        "triggers": [{"mode": "DECISION", "default": True}],
        "implementation": {
            "kind": "WORKFLOW",
            "entrypoint": "schedule",
        },
    }
    created_capability = client.post(
        "/api/v1/capabilities",
        json={
            "name": contract["name"],
            "capability_key": f"brain-capability-{suffix}",
            "contract": contract,
        },
    )
    assert created_capability.status_code == 201, created_capability.text
    capability = created_capability.json()
    capability_draft = client.get(
        f"/api/v1/capabilities/{capability['id']}/draft"
    ).json()
    capability_revision = client.post(
        f"/api/v1/capabilities/{capability['id']}/draft/publish",
        json={
            "draft_revision_id": capability_draft["id"],
            "lock_version": capability_draft["lock_version"],
        },
    )
    assert capability_revision.status_code == 200, capability_revision.text

    composition = {
        "name": f"Brain bundle {suffix}",
        "summary": "Category-oriented reusable brain capability package.",
        "targets": targets,
        "instances": [
            {
                "instance_key": "decision",
                "capability_revision_id": capability_revision.json()["id"],
                "target_ref": f"{targets[0].lower()}:primary",
                "parameters": {},
                "run_policy": {
                    "trigger": "DECISION",
                    "interval_ms": None,
                    "event_types": [],
                },
                "enabled": True,
            }
        ],
        "bindings": [],
    }
    created_bundle = client.post(
        "/api/v1/capability-bundles",
        json={
            "name": composition["name"],
            "bundle_key": f"brain-bundle-{suffix}",
            "composition": composition,
        },
    )
    assert created_bundle.status_code == 201, created_bundle.text
    bundle = created_bundle.json()
    bundle_draft = client.get(
        f"/api/v1/capability-bundles/{bundle['id']}/draft"
    ).json()
    bundle_revision = client.post(
        f"/api/v1/capability-bundles/{bundle['id']}/draft/publish",
        json={
            "draft_revision_id": bundle_draft["id"],
            "lock_version": bundle_draft["lock_version"],
        },
    )
    assert bundle_revision.status_code == 200, bundle_revision.text
    return bundle_revision.json()


def _custom_brain(client: TestClient, name: str) -> tuple[dict, dict]:
    brains = client.get("/api/v1/brains?page_size=100").json()["items"]
    builtin = next(item for item in brains if item["is_builtin"])
    created = client.post(
        "/api/v1/brains",
        json={
            "name": name,
            "source_revision_id": builtin["current_published"]["id"],
        },
    )
    assert created.status_code == 201, created.text
    brain = created.json()
    draft = client.get(f"/api/v1/brains/{brain['id']}/draft").json()
    return brain, draft


def test_brain_revision_mounts_capability_packages_by_category(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        bundle = _published_bundle(client, suffix="schedule-test", targets=["BRAIN"])
        brain, draft = _custom_brain(client, "Capability brain")
        baseline_hash = draft["bundle_hash"]
        extension = {
            "mounts": [
                {
                    "mount_key": "schedule-state",
                    "category": "SCHEDULE_STATE",
                    "capability_bundle_revision_id": bundle["id"],
                    "parameters": {},
                    "enabled": True,
                }
            ],
            "default_reasoning_interval_ms": 30000,
            "legacy_workflow_adapter_enabled": True,
        }
        saved = client.put(
            f"/api/v1/brains/{brain['id']}/draft/capability-extension",
            json={"lock_version": draft["lock_version"], "extension": extension},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["lock_version"] == draft["lock_version"] + 1
        assert client.get(f"/api/v1/brains/{brain['id']}/draft").json()[
            "bundle_hash"
        ] == baseline_hash

        published = client.post(
            f"/api/v1/brains/{brain['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        immutable = client.get(
            f"/api/v1/brains/{brain['id']}/revisions/{published.json()['id']}/capability-extension"
        )
        assert immutable.status_code == 200, immutable.text
        assert immutable.json()["readonly"] is True
        assert immutable.json()["extension"]["mounts"][0]["category"] == "SCHEDULE_STATE"
        forked = client.post(
            f"/api/v1/brains/{brain['id']}/revisions/{published.json()['id']}/fork"
        )
        assert forked.status_code == 201, forked.text
        copied = client.get(
            f"/api/v1/brains/{brain['id']}/draft/capability-extension"
        ).json()
        assert copied["extension"]["mounts"][0]["mount_key"] == "schedule-state"


def test_brain_publish_rejects_bundle_with_wrong_target(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        bundle = _published_bundle(client, suffix="zone-only-test", targets=["ZONE"])
        brain, draft = _custom_brain(client, "Invalid capability brain")
        saved = client.put(
            f"/api/v1/brains/{brain['id']}/draft/capability-extension",
            json={
                "lock_version": draft["lock_version"],
                "extension": {
                    "mounts": [
                        {
                            "mount_key": "zone-only",
                            "category": "PERCEPTION_MEMORY",
                            "capability_bundle_revision_id": bundle["id"],
                            "parameters": {},
                        }
                    ]
                },
            },
        )
        assert saved.status_code == 200, saved.text
        rejected = client.post(
            f"/api/v1/brains/{brain['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert rejected.status_code == 422
        codes = {item["code"] for item in rejected.json()["error"]["details"]["errors"]}
        assert "BRAIN_CAPABILITY_TARGET_MISMATCH" in codes


def test_stanford_brain_uses_default_legacy_adapter_without_mutation(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        brains = client.get("/api/v1/brains?page_size=100").json()["items"]
        builtin = next(item for item in brains if item["is_builtin"])
        extension = client.get(
            f"/api/v1/brains/{builtin['id']}/revisions/{builtin['current_published']['id']}/capability-extension"
        )
        assert extension.status_code == 200, extension.text
        assert extension.json()["is_default"] is True
        assert extension.json()["extension"]["mounts"] == []
        assert extension.json()["extension"]["legacy_workflow_adapter_enabled"] is True


def test_brain_editor_exposes_capability_composition_as_primary_tab():
    html = (ROOT / "generative_agents/web/static/experiment-console.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "generative_agents/web/static/brain-workspace.js").read_text(
        encoding="utf-8"
    )
    assert 'data-brain-editor-tab="capabilities"' in html
    assert 'id="brainCapabilityMountList"' in html
    assert 'id="brainLegacyAdapter"' in html
    assert "readCapabilityExtension()" in script
    assert "/draft/capability-extension" in script
