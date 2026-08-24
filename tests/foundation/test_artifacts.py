from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import select

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import RunEvent
from generative_agents.runtime.artifact_builder import ArtifactBuilder
from generative_agents.runtime.artifact_scheduler import ArtifactSchedulerRepository
from generative_agents.runtime.replay_v2 import GENERATOR_VERSION, validate_replay_v2
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.errors import ServiceError
from generative_agents.services.results import ResultQueryService
from generative_agents.services.runs import RunService


def _published(service, definition: ExperimentDefinition):
    experiment = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = service.get_draft(experiment["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment["experiment_key"]
    draft = service.update_draft(
        experiment_id=experiment["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    revision = service.publish_draft(
        experiment_id=experiment["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    return experiment, revision


def test_persistent_artifact_job_builds_run_scoped_replay_and_deduplicates(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _published(service, publishable_definition)
    var_dir = tmp_path / "var"
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    artifacts = ArtifactService(database, var_dir=var_dir)
    job = artifacts.create_job(run["run_id"], job_type="BUILD_REPLAY")
    claimed = ArtifactSchedulerRepository(database).claim_next()
    assert claimed.job_id == job["job_id"]
    with database.session_factory() as session:
        lifecycle_events = list(
            session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run["run_id"],
                    RunEvent.event_type.in_({"artifact_queued", "artifact_running"}),
                )
                .order_by(RunEvent.id)
            )
        )
    assert [event.event_type for event in lifecycle_events] == [
        "artifact_queued",
        "artifact_running",
    ]
    assert lifecycle_events[0].payload_json["job_id"] == job["job_id"]
    assert ArtifactSchedulerRepository(database).register(
        claimed, pid=os.getpid(), create_time=1.0
    )

    artifact_id = ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])

    finished = artifacts.get_job(job["job_id"])
    assert finished["status"] == "SUCCEEDED"
    assert finished["artifact_id"] == artifact_id
    artifact, path = artifacts.content(run["run_id"], artifact_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert artifact.run_id == run["run_id"]
    # Replay artifacts and live windows share the strict V2 contract. Keep
    # this as a schema/identity assertion rather than a brittle equality check
    # against the superseded four-field V1 document.
    assert validate_replay_v2(document) == document
    assert document["schema_version"] == 2
    assert document["generator_version"] == GENERATOR_VERSION
    assert document["source_kind"] == "RUN_FRAMES"
    assert document["run_id"] == run["run_id"]
    assert document["revision_id"] == revision["id"]
    assert document["source_step"] == document["available_step"] == 0
    assert document["partial"] is True
    assert document["steps"] == []
    assert document["agents"][0]["agent_key"] == "test-agent"
    repeated = artifacts.create_job(run["run_id"], job_type="BUILD_REPLAY")
    assert repeated["job_id"] == job["job_id"]
    assert artifacts.list_artifacts(run["run_id"])["items"][0]["artifact_id"] == artifact_id
    operation_artifact = ResultQueryService(database).operations(run["run_id"])[
        "artifacts"
    ][0]
    assert operation_artifact["generator_version"] == GENERATOR_VERSION
    assert operation_artifact["source_step"] == 0
    assert operation_artifact["partial"] is True


def test_core_artifact_jobs_reject_parameters_that_do_not_affect_content(
    service, database, publishable_definition, tmp_path
):
    experiment, revision = _published(service, publishable_definition)
    run = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )
    artifacts = ArtifactService(database, var_dir=tmp_path / "var")

    with pytest.raises(ServiceError) as caught:
        artifacts.create_job(
            run["run_id"],
            job_type="BUILD_REPLAY",
            parameters={"validation": "probe"},
        )

    assert caught.value.code == "INVALID_ARTIFACT_PARAMETERS"
    assert caught.value.status_code == 422
    assert caught.value.details == {"unknown_parameters": ["validation"]}
