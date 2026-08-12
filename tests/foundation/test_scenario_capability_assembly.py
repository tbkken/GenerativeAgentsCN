from __future__ import annotations

from fastapi.testclient import TestClient
from uuid import UUID

from generative_agents.runtime.multirate import (
    MultiRateCapabilityScheduler,
    ScheduledCapabilityTask,
)
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.manifest import RunManifestStore
from generative_agents.runtime.supervisor import LocalProcessSupervisor
from generative_agents.web import create_app
from generative_agents.services import ServiceError


def _publish_fixed_bundle(client: TestClient) -> dict:
    contract = {
        "name": "Fast traffic controller",
        "summary": "A deterministic high-frequency traffic capability.",
        "kind": "CONTROLLER",
        "targets": ["AGENT", "TOOL", "INTERACTION"],
        "triggers": [
            {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
        ],
        "implementation": {
            "kind": "RULES",
            "source": "return {}",
        },
    }
    capability = client.post(
        "/api/v1/capabilities",
        json={
            "name": contract["name"],
            "capability_key": "fast-traffic-controller-test",
            "contract": contract,
        },
    ).json()
    capability_draft = client.get(
        f"/api/v1/capabilities/{capability['id']}/draft"
    ).json()
    capability_revision = client.post(
        f"/api/v1/capabilities/{capability['id']}/draft/publish",
        json={
            "draft_revision_id": capability_draft["id"],
            "lock_version": capability_draft["lock_version"],
        },
    ).json()
    composition = {
        "name": "Fast traffic package",
        "summary": "Reusable fixed-rate traffic controller package.",
        "targets": ["INTERACTION"],
        "instances": [
            {
                "instance_key": "controller",
                "capability_revision_id": capability_revision["id"],
                "target_ref": "interaction:crossing",
                "parameters": {},
                "run_policy": {
                    "trigger": "FIXED_INTERVAL",
                    "interval_ms": 200,
                    "event_types": [],
                },
            }
        ],
        "bindings": [],
    }
    bundle = client.post(
        "/api/v1/capability-bundles",
        json={
            "name": composition["name"],
            "bundle_key": "fast-traffic-package-test",
            "composition": composition,
        },
    ).json()
    bundle_draft = client.get(
        f"/api/v1/capability-bundles/{bundle['id']}/draft"
    ).json()
    published = client.post(
        f"/api/v1/capability-bundles/{bundle['id']}/draft/publish",
        json={
            "draft_revision_id": bundle_draft["id"],
            "lock_version": bundle_draft["lock_version"],
        },
    )
    assert published.status_code == 200, published.text
    return published.json()


def test_capability_assembly_keeps_legacy_definition_hash_and_compiles_schedule(
    database_url,
):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        bundle = _publish_fixed_bundle(client)
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "One car one pedestrian", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{experiment['id']}/draft").json()
        original_hash = draft["definition_hash"]
        agent_key = draft["definition"]["agents"][0]["agent_key"]
        map_revision_id = draft["definition"]["world"]["map_revision_id"]
        car = next(
            item
            for item in client.get("/api/v1/tools?page_size=100").json()["items"]
            if item["tool_key"] == "generic-car"
        )
        extension = {
            "mode": "CAPABILITY_COMPOSED",
            "map_revision_id": map_revision_id,
            "clock": {
                "base_tick_ms": 100,
                "duration_ms": 10000,
                "snapshot_interval_ms": 200,
            },
            "actors": [
                {
                    "actor_key": "driver",
                    "experiment_agent_key": agent_key,
                    "role": "DRIVER",
                    "initial_pose": {"x_m": 0, "y_m": 0, "heading_degrees": 0},
                    "reasoning_interval_ms": 1000,
                    "active_tool_instance_key": "car-one",
                }
            ],
            "tool_instances": [
                {
                    "instance_key": "car-one",
                    "tool_revision_id": car["current_published"]["id"],
                    "owner_actor_key": "driver",
                    "operator_actor_key": "driver",
                    "initial_pose": {"x_m": 0, "y_m": 0, "heading_degrees": 0},
                }
            ],
            "capability_mounts": [
                {
                    "mount_key": "crossing-control",
                    "capability_bundle_revision_id": bundle["id"],
                    "target_bindings": {"primary": "interaction:crossing"},
                    "parameters": {},
                    "input_bindings": {},
                    "output_bindings": {},
                }
            ],
            "metrics": [
                {
                    "metric_key": "minimum-distance",
                    "kind": "MINIMUM_DISTANCE",
                    "unit": "m",
                    "collision_threshold_m": 0.5,
                }
            ],
        }
        saved = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly",
            json={"lock_version": draft["lock_version"], "extension": extension},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["schedule"]["tasks"][0]["interval_ms"] == 200
        assert saved.json()["schedule"]["tasks"][0]["estimated_executions"] == 50
        draft_after = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft"
        ).json()
        assert draft_after["definition_hash"] == original_hash

        report = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly/validate"
        )
        assert report.status_code == 200, report.text
        assert report.json()["valid"] is True
        assert report.json()["schedule"]["total_executions"] == 50


def test_new_experiment_defaults_to_unmodified_legacy_town_mode(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "Legacy compatibility", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        assembly = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly"
        )
        assert assembly.status_code == 200, assembly.text
        assert assembly.json()["is_default"] is True
        assert assembly.json()["extension"]["mode"] == "LEGACY_TOWN"
        assert assembly.json()["schedule"]["tasks"] == []


def test_reusable_three_lane_intersection_map_is_seeded_from_spatial_assets(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        maps = client.get("/api/v1/maps?page_size=100").json()["items"]
        intersection = next(
            item for item in maps if item["map_key"] == "standard-3lane-intersection"
        )
        revision = client.get(
            f"/api/v1/maps/{intersection['id']}/revisions/{intersection['current_published']['id']}"
        ).json()
        definition = revision["world"]["definition"]
        assert definition["size"] == [48, 48]
        assert len(definition["tiles"]) == 48 * 48
        scene = definition["spatial_scene"]
        assert set(scene["palette_refs"]) == {
            "ground",
            "road",
            "sidewalk",
            "crosswalk",
        }
        assert len([item for item in scene["placements"] if item["instance_key"].startswith("signal-")]) == 4
        assert len([item for item in scene["placements"] if item["instance_key"].startswith("wait-")]) == 4


def test_versioned_one_car_one_pedestrian_template_applies_by_actor_slots(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "Template application", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{experiment['id']}/draft").json()
        agent_keys = [item["agent_key"] for item in draft["definition"]["agents"][:2]]
        templates = client.get("/api/v1/scenario-templates").json()
        template = next(
            item
            for item in templates["items"]
            if item["template_key"] == "one-car-one-pedestrian"
        )
        revision = template["current_published"]
        assert revision["readonly"] is True
        assert revision["contract"]["schema_version"] == "ga-scenario-template/v1"
        applied = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/scenario-templates/{revision['id']}/apply",
            json={
                "lock_version": draft["lock_version"],
                "actor_bindings": {
                    "pedestrian": agent_keys[0],
                    "driver": agent_keys[1],
                },
                "clock_overrides": {"duration_ms": 12_000},
            },
        )
        assert applied.status_code == 200, applied.text
        extension = applied.json()["extension"]
        assert extension["mode"] == "CAPABILITY_COMPOSED"
        assert extension["clock"]["duration_ms"] == 12_000
        assert {item["experiment_agent_key"] for item in extension["actors"]} == set(
            agent_keys
        )
        assert len(extension["capability_mounts"]) == 5
        report = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly/validate"
        ).json()
        assert report["valid"] is True, report
        assert report["schedule"]["estimated_llm_decisions"] == 0
        inherited = [
            item
            for item in report["schedule"]["tasks"]
            if item["source_kind"] == "SPATIAL_ASSET_ATTACHMENT"
        ]
        assert len(inherited) == 4
        assert {item["source_ref"] for item in inherited} == {
            "wait-north-west",
            "wait-north-east",
            "wait-south-east",
            "wait-south-west",
        }
        estimate = client.get(
            f"/api/v1/experiments/{experiment['id']}/run-estimate"
        ).json()
        assert estimate["scale"]["execution_mode"] == "CAPABILITY_COMPOSED"
        assert estimate["scale"]["agents"] == 2
        assert estimate["scale"]["tool_instances"] == 1
        assert estimate["scale"]["steps"] == 12
        assert estimate["scale"]["duration_ms"] == 12_000
        assert estimate["scale"]["base_tick_ms"] == 100
        assert estimate["scale"]["world_size"] == [48, 48]
        assert estimate["estimate"]["model_calls"] == {"low": 0, "high": 0}
        assert estimate["estimate"]["tokens"] == {"low": 0, "high": 0}
        assert estimate["high_scale"] is False
        list_item = next(
            item
            for item in client.get("/api/v1/experiments?page_size=50").json()[
                "items"
            ]
            if item["id"] == experiment["id"]
        )
        assert list_item["core_parameters"]["execution_mode"] == "CAPABILITY_COMPOSED"
        assert list_item["core_parameters"]["agent_count"] == 2
        assert list_item["core_parameters"]["tool_count"] == 1
        assert list_item["core_parameters"]["duration_ms"] == 12_000
        assert list_item["core_parameters"]["base_tick_ms"] == 100
        assert list_item["core_parameters"]["requires_models"] is False
        assert list_item["core_parameters"]["world_name"] == "标准四向三车道路口"
        publish_gate = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/validate"
        ).json()
        assert publish_gate["valid"] is True, publish_gate
        assert publish_gate["auto_model_probe"] == {
            "enabled": False,
            "purposes": [],
            "count": 0,
        }

        saved_template = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/scenario-templates",
            json={
                "name": "Reusable crossing experiment",
                "description": "Saved from a real composed experiment draft.",
            },
        )
        assert saved_template.status_code == 201, saved_template.text
        saved_template = saved_template.json()
        template_draft = saved_template["current_draft"]
        assert template_draft["state"] == "DRAFT"
        assert {
            item["slot_key"] for item in template_draft["contract"]["actor_slots"]
        } == {"pedestrian", "driver"}
        published_template = client.post(
            f"/api/v1/scenario-templates/{saved_template['id']}/draft/publish",
            json={
                "draft_revision_id": template_draft["id"],
                "lock_version": template_draft["lock_version"],
            },
        )
        assert published_template.status_code == 200, published_template.text
        assert published_template.json()["readonly"] is True
        forked_template = client.post(
            f"/api/v1/scenario-templates/{saved_template['id']}/revisions/"
            f"{published_template.json()['id']}/fork"
        )
        assert forked_template.status_code == 201, forked_template.text
        assert forked_template.json()["revision_no"] == 2
        assert forked_template.json()["base_revision_id"] == published_template.json()[
            "id"
        ]

        latest_draft = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft"
        ).json()
        models = latest_draft["definition"]["models"]
        models["chat"]["model"] = "deterministic-chat-test"
        models["chat"]["resolved_model"] = "deterministic-chat-test"
        models["embedding"]["model"] = "deterministic-embedding-test"
        models["embedding"]["resolved_model"] = "deterministic-embedding-test"
        model_update = client.patch(
            f"/api/v1/experiments/{experiment['id']}/draft/models",
            json={"lock_version": latest_draft["lock_version"], "data": models},
        )
        assert model_update.status_code == 200, model_update.text
        latest_draft = model_update.json()
        publish_report = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/validate"
        ).json()
        assert publish_report["valid"] is True, publish_report
        try:
            published = app.state.experiment_service.publish_draft(
                experiment_id=experiment["id"],
                draft_revision_id=latest_draft["id"],
                expected_lock_version=latest_draft["lock_version"],
            )
        except ServiceError as exc:  # pragma: no cover - diagnostic assertion
            raise AssertionError(exc.details) from exc
        run = app.state.run_service.create_from_published(
            experiment["id"], published["id"]
        )
        assert run["requested_steps"] == 12
        assert run["stride_minutes"] == 1
        supervisor = LocalProcessSupervisor(
            app.state.database,
            var_dir=app.state.run_service._var_dir,
            code_build_id="capability-template-test",
        )
        claimed = supervisor.repository.claim_next()
        assert claimed is not None
        supervisor._materialize_manifest(claimed)
        manifest = RunManifestStore(
            RunPaths.under(app.state.run_service._var_dir, UUID(run["run_id"]))
        ).load_verified()
        assert manifest.capability_snapshot is not None
        assert manifest.capability_snapshot["experiment_extension"]["mode"] == "CAPABILITY_COMPOSED"
        assert len(manifest.capability_snapshot["capability_bundles"]) == 4


def test_deterministic_composed_publish_skips_unused_model_probes(
    database_url, monkeypatch
):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "No model traffic run", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{experiment['id']}/draft").json()
        template = next(
            item
            for item in client.get("/api/v1/scenario-templates").json()["items"]
            if item["template_key"] == "one-car-one-pedestrian"
        )
        agent_keys = [item["agent_key"] for item in draft["definition"]["agents"][:2]]
        applied = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/scenario-templates/"
            f"{template['current_published']['id']}/apply",
            json={
                "lock_version": draft["lock_version"],
                "actor_bindings": {
                    "pedestrian": agent_keys[0],
                    "driver": agent_keys[1],
                },
                "clock_overrides": {"duration_ms": 2_000},
            },
        )
        assert applied.status_code == 200, applied.text
        latest_draft = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft"
        ).json()

        def unexpected_probe(*_args, **_kwargs):
            raise AssertionError("deterministic capability graph must not probe models")

        monkeypatch.setattr(
            app.state.model_probe_service,
            "resolve_for_publish",
            unexpected_probe,
        )
        response = client.post(
            f"/api/v1/experiments/{experiment['id']}/actions/publish-and-run",
            json={
                "draft_revision_id": latest_draft["id"],
                "lock_version": latest_draft["lock_version"],
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["requested_steps"] == 2


def test_multirate_scheduler_runs_dynamics_more_often_than_reasoning():
    calls: list[tuple[str, int]] = []
    scheduler = MultiRateCapabilityScheduler(base_tick_ms=100)
    scheduler.register(
        ScheduledCapabilityTask(
            task_key="vehicle-dynamics",
            trigger="FIXED_INTERVAL",
            interval_ms=100,
            priority=40,
            callback=lambda invocation: calls.append(
                (invocation.task_key, invocation.virtual_time_ms)
            ),
        )
    )
    scheduler.register(
        ScheduledCapabilityTask(
            task_key="driver-reasoning",
            trigger="DECISION",
            interval_ms=1000,
            priority=20,
            callback=lambda invocation: calls.append(
                (invocation.task_key, invocation.virtual_time_ms)
            ),
        )
    )
    summary = scheduler.run(2000)
    assert summary.invocation_counts["vehicle-dynamics"] == 20
    assert summary.invocation_counts["driver-reasoning"] == 2
    assert summary.ticks == 20


def test_event_tasks_run_only_when_matching_events_exist():
    event_sizes: list[int] = []
    scheduler = MultiRateCapabilityScheduler(base_tick_ms=100)
    scheduler.register(
        ScheduledCapabilityTask(
            task_key="gate-controller",
            trigger="EVENT",
            event_types=("event/card_scanned",),
            callback=lambda invocation: event_sizes.append(len(invocation.events)),
        )
    )
    scheduler.run(100)
    assert event_sizes == []
    scheduler.publish_event("event/card_scanned", {"card": "staff"})
    scheduler.run(100)
    assert event_sizes == [1]
