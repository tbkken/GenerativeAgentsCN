from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from generative_agents.persistence.models import ExperimentRevision
from generative_agents.runtime.capability_simulation import build_capability_runner
from generative_agents.runtime.capability_snapshot import (
    build_capability_runtime_snapshot,
)
from generative_agents.runtime.context import RunControl, SimulationClock
from generative_agents.web import create_app


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'capability-runtime.db').as_posix()}"


class _MemoryCommitter:
    def __init__(self) -> None:
        self.results = []

    def commit(self, result, *, force_checkpoint: bool):
        self.results.append((result, force_checkpoint))


def _published_by_key(items, key_name, key):
    item = next(value for value in items if value[key_name] == key)
    return item["current_published"]["id"]


def test_one_car_one_pedestrian_executes_published_capability_graph(tmp_path):
    app = create_app(database_url=_database_url(tmp_path), supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "Capability runtime", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{experiment['id']}/draft").json()
        agent_keys = [item["agent_key"] for item in draft["definition"]["agents"][:2]]
        assert len(agent_keys) == 2

        maps = client.get("/api/v1/maps?page_size=100").json()["items"]
        map_revision_id = _published_by_key(
            maps, "map_key", "standard-3lane-intersection"
        )
        tools = client.get("/api/v1/tools?page_size=100").json()["items"]
        car_revision_id = _published_by_key(tools, "tool_key", "generic-car")
        bundles = client.get(
            "/api/v1/capability-bundles?page_size=100"
        ).json()["items"]
        bundle_ids = {
            key: _published_by_key(bundles, "bundle_key", key)
            for key in (
                "relative-motion-perception",
                "pedestrian-crossing-behavior",
                "vehicle-yield-behavior",
                "crossing-safety-observation",
            )
        }
        extension = {
            "mode": "CAPABILITY_COMPOSED",
            "map_revision_id": map_revision_id,
            "clock": {
                "base_tick_ms": 100,
                "duration_ms": 8_000,
                "snapshot_interval_ms": 1_000,
            },
            "actors": [
                {
                    "actor_key": "pedestrian",
                    "experiment_agent_key": agent_keys[0],
                    "role": "PEDESTRIAN",
                    "initial_pose": {"x_m": 24, "y_m": 18, "heading_degrees": 90},
                    "route": [{"x_m": 24, "y_m": 36, "heading_degrees": 90}],
                    "reasoning_interval_ms": 200,
                },
                {
                    "actor_key": "driver",
                    "experiment_agent_key": agent_keys[1],
                    "role": "DRIVER",
                    "initial_pose": {"x_m": 8, "y_m": 24, "heading_degrees": 0},
                    "reasoning_interval_ms": 200,
                    "active_tool_instance_key": "car-one",
                },
            ],
            "tool_instances": [
                {
                    "instance_key": "car-one",
                    "tool_revision_id": car_revision_id,
                    "owner_actor_key": "driver",
                    "operator_actor_key": "driver",
                    "initial_pose": {"x_m": 8, "y_m": 24, "heading_degrees": 0},
                    "route": [{"x_m": 40, "y_m": 24, "heading_degrees": 0}],
                    "state_overrides": {"speed_mps": 4},
                }
            ],
            "capability_mounts": [
                {
                    "mount_key": "pedestrian-perception",
                    "capability_bundle_revision_id": bundle_ids[
                        "relative-motion-perception"
                    ],
                    "target_bindings": {"crossing": "interaction:crossing"},
                    "input_bindings": {
                        "subject_motion": "state:actor:pedestrian:motion",
                        "object_motion": "state:tool:car-one:motion",
                    },
                    "output_bindings": {
                        "relative_motion": "channel:pedestrian-relative-motion"
                    },
                },
                {
                    "mount_key": "vehicle-perception",
                    "capability_bundle_revision_id": bundle_ids[
                        "relative-motion-perception"
                    ],
                    "target_bindings": {"crossing": "interaction:crossing"},
                    "input_bindings": {
                        "subject_motion": "state:tool:car-one:motion",
                        "object_motion": "state:actor:pedestrian:motion",
                    },
                    "output_bindings": {
                        "relative_motion": "channel:vehicle-relative-motion"
                    },
                },
                {
                    "mount_key": "pedestrian-behavior",
                    "capability_bundle_revision_id": bundle_ids[
                        "pedestrian-crossing-behavior"
                    ],
                    "target_bindings": {"subject": "actor:pedestrian"},
                    "input_bindings": {
                        "relative_motion": "channel:pedestrian-relative-motion"
                    },
                    "output_bindings": {"motion": "channel:pedestrian-motion"},
                },
                {
                    "mount_key": "vehicle-behavior",
                    "capability_bundle_revision_id": bundle_ids[
                        "vehicle-yield-behavior"
                    ],
                    "target_bindings": {
                        "subject": "actor:driver",
                        "vehicle": "tool:car-one",
                    },
                    "input_bindings": {
                        "relative_motion": "channel:vehicle-relative-motion",
                        "current_motion": "state:tool:car-one:motion",
                        "route": "state:tool:car-one:route",
                    },
                    "output_bindings": {"motion": "channel:vehicle-motion"},
                },
                {
                    "mount_key": "safety-observation",
                    "capability_bundle_revision_id": bundle_ids[
                        "crossing-safety-observation"
                    ],
                    "target_bindings": {"crossing": "interaction:crossing"},
                    "input_bindings": {
                        "motions": "state:interaction:crossing:motions"
                    },
                    "output_bindings": {
                        "minimum_distance": "channel:minimum-distance"
                    },
                },
            ],
            "metrics": [
                {
                    "metric_key": "minimum-distance",
                    "kind": "MINIMUM_DISTANCE",
                    "source_channel": "channel:minimum-distance",
                    "unit": "m",
                }
            ],
        }
        saved = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly",
            json={"lock_version": draft["lock_version"], "extension": extension},
        )
        assert saved.status_code == 200, saved.text
        report = client.post(
            f"/api/v1/experiments/{experiment['id']}/draft/capability-assembly/validate"
        )
        assert report.status_code == 200, report.text
        assert report.json()["valid"] is True, report.json()

        database = app.state.database
        with database.session_factory() as session:
            revision = session.get(ExperimentRevision, saved.json()["revision_id"])
            snapshot = build_capability_runtime_snapshot(session, revision)
        assert snapshot is not None
        assert len(snapshot["capabilities"]) >= 7

        definition = draft["definition"]
        def make_context(run_id, attempt_id):
            return SimpleNamespace(
                run_id=run_id,
                attempt_id=attempt_id,
                clock=SimulationClock(
                    __import__("datetime").datetime.fromisoformat(
                        definition["simulation"]["start_time"]
                    )
                ),
                control=RunControl(),
                models=None,
            )

        run_id = uuid4()
        committer = _MemoryCommitter()
        context = make_context(run_id, uuid4())
        runner = build_capability_runner(
            context,
            snapshot,
            checkpoint_interval_steps=1,
            committer=committer,
        )
        assert runner.run(8) == 8
        assert len(committer.results) == 8
        last = committer.results[-1][0]
        assert {item.agent_key for item in last.agents} == set(agent_keys)
        snapshots = [
            event
            for result, _ in committer.results
            for event in result.domain_events
            if event.event_type == "capability.snapshot"
        ]
        assert len(snapshots) == 8
        assert snapshots[-1].payload["trajectory_samples"]
        execution_events = [
            event
            for result, _ in committer.results
            for event in result.domain_events
            if event.event_type == "capability.execution-batch"
        ]
        statuses = {
            execution["status"]
            for event in execution_events
            for execution in event.payload["executions"]
        }
        assert statuses == {"SUCCEEDED"}
        attachment_tasks = {
            execution["task_key"]
            for event in execution_events
            for execution in event.payload["executions"]
            if execution["task_key"].startswith("spatial-")
        }
        assert len(attachment_tasks) == 4
        presence_channels = {
            key
            for key in snapshots[-1].payload["channels"]
            if key.startswith("state:zone:wait-") and key.endswith(":presence")
        }
        assert len(presence_channels) == 4
        assert any(
            event.event_type == "traffic.passage-decision"
            for result, _ in committer.results
            for event in result.domain_events
        )

        def snapshot_payloads(memory_committer):
            return [
                event.payload
                for result, _ in memory_committer.results
                for event in result.domain_events
                if event.event_type == "capability.snapshot"
            ]

        reference_payloads = snapshot_payloads(committer)
        repeated = _MemoryCommitter()
        repeated_runner = build_capability_runner(
            make_context(run_id, uuid4()),
            snapshot,
            checkpoint_interval_steps=1,
            committer=repeated,
        )
        assert repeated_runner.run(8) == 8
        assert snapshot_payloads(repeated) == reference_payloads

        before_resume = _MemoryCommitter()
        partial_runner = build_capability_runner(
            make_context(run_id, uuid4()),
            snapshot,
            checkpoint_interval_steps=1,
            committer=before_resume,
        )
        assert partial_runner.run(4) == 4
        checkpoint_state = partial_runner.snapshot_state()
        after_resume = _MemoryCommitter()
        resumed_runner = build_capability_runner(
            make_context(run_id, uuid4()),
            snapshot,
            checkpoint_state=checkpoint_state,
            checkpoint_interval_steps=1,
            committer=after_resume,
        )
        resumed_runner.completed_steps = 4
        assert resumed_runner.run(4) == 8
        assert (
            snapshot_payloads(before_resume) + snapshot_payloads(after_resume)
            == reference_payloads
        )
