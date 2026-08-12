from __future__ import annotations

import copy
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import ExperimentRevision
from generative_agents.services import ServiceError


def _create_publishable(service, definition: ExperimentDefinition):
    created = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = service.get_draft(created["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = created["experiment_key"]
    updated = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    return created, updated


def test_alembic_upgrade_creates_core_tables_and_sqlite_pragmas(database):
    with database.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert {
            "experiments",
            "experiment_revisions",
            "runs",
            "run_queue",
            "run_attempts",
            "run_events",
            "secrets",
            "assets",
            "run_artifacts",
            "artifact_jobs",
            "run_result_summaries",
            "run_steps",
            "run_agent_steps",
            "run_conversations",
            "run_messages",
                "run_memory_events",
                "builtin_catalog_snapshots",
                "world_maps",
                "world_map_revisions",
                "experiment_workflows",
                "experiment_workflow_versions",
                "model_probe_statuses",
                "experiment_saved_views",
                "experiment_comparison_groups",
                "agent_templates",
                "agent_template_revisions",
                "crowd_templates",
                "crowd_revisions",
                "crowd_revision_members",
            } <= tables
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
            ).scalar() == "0022_scenario_templates"


def test_create_and_list_experiments_isolated_and_paginated(service):
    first = service.create_experiment(name="Alpha", goal="memory", source_type="BLANK")
    second = service.create_experiment(name="Beta", goal="social", source_type="BLANK")
    assert first["id"] != second["id"]
    assert first["current_draft"]["id"] != second["current_draft"]["id"]

    result = service.list_experiments(query="memory", page=1, page_size=10)
    assert result["total"] == 1
    assert result["items"][0]["id"] == first["id"]
    assert result["status_counts"]["DRAFT"] == 1
    assert result["status_counts"]["ALL"] == 1
    five_item_page = service.list_experiments(page=1, page_size=5)
    assert five_item_page["page_size"] == 5


def test_builtin_template_is_materialized_once_per_independent_draft(service):
    first = service.create_experiment(name="标准实验 A", source_type="BUILTIN_DEFAULT")
    second = service.create_experiment(name="标准实验 B", source_type="BUILTIN_DEFAULT")
    first_draft = service.get_draft(first["id"])
    second_draft = service.get_draft(second["id"])

    assert len(first_draft["definition"]["agents"]) == 25
    assert first_draft["definition"]["world"]["world_name"] == "the Ville"
    assert first_draft["definition"]["prompts"]["base_desc"]["content"].strip()
    changed = ExperimentDefinition.model_validate(first_draft["definition"])
    changed.simulation.random_seed = 20260808
    service.update_draft(
        experiment_id=first["id"],
        expected_lock_version=first_draft["lock_version"],
        definition=changed,
    )
    assert (
        service.get_draft(second["id"])["definition"]["simulation"]["random_seed"]
        != 20260808
    )


def test_stale_draft_save_returns_revision_conflict(service):
    created = service.create_experiment(name="Conflict", source_type="BLANK")
    draft = service.get_draft(created["id"])
    definition = ExperimentDefinition.model_validate(draft["definition"])
    updated = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=1,
        definition=definition,
    )
    assert updated["lock_version"] == 2
    with pytest.raises(ServiceError) as exc:
        service.update_draft(
            experiment_id=created["id"],
            expected_lock_version=1,
            definition=definition,
        )
    assert exc.value.code == "REVISION_CONFLICT"
    assert exc.value.details == {"expected_lock_version": 1, "actual_lock_version": 2}


def test_published_revision_is_immutable_at_database_layer(
    service, database, publishable_definition
):
    created, draft = _create_publishable(service, publishable_definition)
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    assert published["state"] == "PUBLISHED"
    assert published["snapshot_complete"] is True

    with pytest.raises(IntegrityError, match="PUBLISHED_REVISION_IMMUTABLE"):
        with database.session_factory.begin() as session:
            session.execute(
                update(ExperimentRevision)
                .where(ExperimentRevision.id == published["id"])
                .values(definition_hash="0" * 64)
            )
    with pytest.raises(IntegrityError, match="PUBLISHED_REVISION_IMMUTABLE"):
        with database.session_factory.begin() as session:
            revision = session.get(ExperimentRevision, published["id"])
            session.delete(revision)


def test_fork_published_revision_is_a_deep_independent_draft(
    service, database, publishable_definition
):
    created, draft = _create_publishable(service, publishable_definition)
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    fork = service.fork_revision(created["id"], published["id"])
    assert fork["state"] == "DRAFT"
    assert fork["base_revision_id"] == published["id"]
    assert fork["definition_hash"] == published["definition_hash"]

    changed = copy.deepcopy(fork["definition"])
    changed["simulation"]["random_seed"] += 1
    service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=fork["lock_version"],
        definition=ExperimentDefinition.model_validate(changed),
    )
    assert service.get_revision(created["id"], published["id"])["definition"][
        "simulation"
    ]["random_seed"] == publishable_definition.simulation.random_seed


def test_database_allows_only_one_draft_per_experiment(service, database):
    created = service.create_experiment(name="One draft", source_type="BLANK")
    current = service.get_draft(created["id"])
    duplicate = ExperimentRevision(
        id=str(uuid4()),
        experiment_id=created["id"],
        revision_no=2,
        state="DRAFT",
        schema_version=1,
        definition_json=current["definition"],
        definition_hash=current["definition_hash"],
        provenance_json={},
        snapshot_complete=False,
        lock_version=1,
    )
    with pytest.raises(IntegrityError):
        with database.session_factory.begin() as session:
            session.add(duplicate)


def test_publish_rejects_auto_model_without_resolved_identity(service):
    created = service.create_experiment(name="Unresolved", source_type="BLANK")
    draft = service.get_draft(created["id"])
    with pytest.raises(ServiceError) as exc:
        service.publish_draft(
            experiment_id=created["id"],
            draft_revision_id=draft["id"],
            expected_lock_version=draft["lock_version"],
        )
    assert exc.value.code == "CONFIG_VALIDATION_FAILED"
    codes = {item["code"] for item in exc.value.details["errors"]}
    assert "MODEL_NOT_RESOLVED" in codes
