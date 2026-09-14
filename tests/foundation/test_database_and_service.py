"""基础能力回归测试：覆盖 ``test_database_and_service`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import copy
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.models import ExperimentRevision
from generative_agents.services import ServiceError
from generative_agents.services.maps import WorldMapService
from generative_agents.services.spatial_assets import SpatialAssetService
from generative_agents.skills import SkillRegistry
from tests.support import brain_selection_for_database, publish_user_map


def _create_publishable(service, definition: ExperimentDefinition):
    """为本测试模块封装 ``_create_publishable`` 辅助步骤，减少重复的场景搭建代码。"""
    map_revision = publish_user_map(service.database, world=definition.world)
    created = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
        map_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    draft = service.get_draft(created["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = created["experiment_key"]
    payload["world"] = draft["definition"]["world"]
    payload["engine"] = draft["definition"]["engine"]
    updated = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    return created, updated


def test_alembic_upgrade_creates_core_tables_and_sqlite_pragmas(database):
    """回归验证 ``test_alembic_upgrade_creates_core_tables_and_sqlite_pragmas`` 所描述的业务结果、故障边界和隔离约束。"""
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
                "run_step_effects",
                "world_maps",
                "spatial_asset_definitions",
                "model_probe_statuses",
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
                ).scalar() == "0001_mutable_resource_baseline"


def test_create_and_list_experiments_isolated_and_paginated(service):
    """回归验证 ``test_create_and_list_experiments_isolated_and_paginated`` 所描述的业务结果、故障边界和隔离约束。"""
    map_revision = publish_user_map(service.database)
    first = service.create_experiment(
        name="Alpha", goal="memory", source_type="BLANK",
        map_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    second = service.create_experiment(
        name="Beta", goal="social", source_type="BLANK",
        map_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    assert first["id"] != second["id"]
    assert first["current_draft"]["id"] != second["current_draft"]["id"]

    result = service.list_experiments(page=1)
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [second["id"], first["id"]]
    assert result["page_size"] == 5


def test_selected_user_map_is_materialized_once_per_independent_draft(service):
    """Two drafts may share one published user map without sharing mutable state."""
    map_revision = publish_user_map(service.database, name="共享用户地图")
    first = service.create_experiment(
        name="标准实验 A", source_type="BLANK",
        map_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    second = service.create_experiment(
        name="标准实验 B", source_type="BLANK",
        map_id=map_revision["id"],
        **brain_selection_for_database(service.database),
    )
    first_draft = service.get_draft(first["id"])
    second_draft = service.get_draft(second["id"])

    assert first_draft["definition"]["agents"] == []
    assert first_draft["definition"]["world"]["world_name"] == "共享用户地图"
    assert "prompts" not in first_draft["definition"]
    assert SkillRegistry().prompt("base-desc").strip()
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
    """回归验证 ``test_stale_draft_save_returns_revision_conflict`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(
        name="Conflict", source_type="BLANK",
        map_id=publish_user_map(service.database)["id"],
        **brain_selection_for_database(service.database),
    )
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
    """回归验证 ``test_published_revision_is_immutable_at_database_layer`` 所描述的业务结果、故障边界和隔离约束。"""
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


def test_published_experiment_freezes_map_and_asset_closure(
    service, database, publishable_definition
):
    """Later map/asset edits affect live maps but never a published experiment."""
    assets = SpatialAssetService(database)
    asset = assets.create_asset(
        name="Snapshot road",
        asset_key=f"snapshot-road-{uuid4().hex[:8]}",
        asset_kind="TILE",
    )
    payload = publishable_definition.model_dump(mode="json", exclude_none=False)
    payload["world"]["definition"]["spatial_scene"] = {
        "schema_version": "ga-spatial-scene/v2",
        "palette_refs": {"road": asset["id"]},
        "placements": [],
    }
    definition = ExperimentDefinition.model_validate(payload)
    created, draft = _create_publishable(service, definition)
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    frozen_color = published["definition"]["world"]["definition"]["editor"][
        "spatial_assets"
    ][asset["id"]]["appearance"]["color"]
    frozen_hash = published["definition"]["world"]["map_snapshot_hash"]

    changed_contract = copy.deepcopy(asset["contract"])
    changed_contract["appearance"]["color"] = "#123456"
    assets.update_asset(
        asset["id"],
        expected_row_version=asset["row_version"],
        contract=changed_contract,
    )
    map_id = draft["definition"]["world"]["map_id"]
    live_map = WorldMapService(database).get_map(map_id)
    frozen_again = service.get_revision(created["id"], published["id"])

    assert frozen_color != "#123456"
    assert live_map["world"]["definition"]["editor"]["spatial_assets"][asset["id"]][
        "appearance"
    ]["color"] == "#123456"
    assert frozen_again["definition"]["world"]["definition"]["editor"][
        "spatial_assets"
    ][asset["id"]]["appearance"]["color"] == frozen_color
    assert frozen_again["definition"]["world"]["map_snapshot_hash"] == frozen_hash


def test_experiment_service_can_delete_the_whole_published_resource_tree(
    service, publishable_definition
):
    created, draft = _create_publishable(service, publishable_definition)
    service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )

    service.delete_experiment(created["id"])

    with pytest.raises(ServiceError) as exc:
        service.get_experiment(created["id"])
    assert exc.value.code == "EXPERIMENT_NOT_FOUND"


def test_fork_published_revision_is_a_deep_independent_draft(
    service, database, publishable_definition
):
    """回归验证 ``test_fork_published_revision_is_a_deep_independent_draft`` 所描述的业务结果、故障边界和隔离约束。"""
    created, draft = _create_publishable(service, publishable_definition)
    with database.session_factory.begin() as session:
        draft_row = session.get(ExperimentRevision, draft["id"])
        draft_row.provenance_json = {
            **(draft_row.provenance_json or {}),
            "brain_revision_id": "brain-revision-fixture",
        }
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    fork = service.fork_revision(created["id"], published["id"])
    assert fork["state"] == "DRAFT"
    assert fork["base_revision_id"] == published["id"]
    assert fork["definition_hash"] == published["definition_hash"]
    assert fork["provenance"]["brain_revision_id"] == "brain-revision-fixture"

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


def test_forked_simulation_can_change_run_settings_but_not_fixed_resources(
    service, publishable_definition
):
    created, draft = _create_publishable(service, publishable_definition)
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    fork = service.fork_revision(created["id"], published["id"])

    simulation_change = copy.deepcopy(fork["definition"])
    simulation_change["simulation"]["random_seed"] += 1
    simulation_change["models"]["chat"]["temperature"] = 0.35
    saved = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=fork["lock_version"],
        definition=ExperimentDefinition.model_validate(simulation_change),
    )
    assert saved["definition"]["simulation"]["random_seed"] == (
        fork["definition"]["simulation"]["random_seed"] + 1
    )
    assert saved["definition"]["models"]["chat"]["temperature"] == 0.35

    fixed_resource_change = copy.deepcopy(saved["definition"])
    fixed_resource_change["agents"][0]["enabled"] = not fixed_resource_change[
        "agents"
    ][0]["enabled"]
    with pytest.raises(ServiceError) as exc:
        service.update_draft(
            experiment_id=created["id"],
            expected_lock_version=saved["lock_version"],
            definition=ExperimentDefinition.model_validate(fixed_resource_change),
        )
    assert exc.value.code == "EXPERIMENT_RESOURCES_LOCKED"
    assert exc.value.details == {"fixed_sections": ["agents"]}


def test_database_allows_only_one_draft_per_experiment(service, database):
    """回归验证 ``test_database_allows_only_one_draft_per_experiment`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(
        name="One draft", source_type="BLANK",
        map_id=publish_user_map(service.database)["id"],
        **brain_selection_for_database(service.database),
    )
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
    """回归验证 ``test_publish_rejects_auto_model_without_resolved_identity`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(
        name="Unresolved", source_type="BLANK",
        map_id=publish_user_map(service.database)["id"],
        **brain_selection_for_database(service.database),
    )
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
