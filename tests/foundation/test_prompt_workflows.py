from __future__ import annotations

import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from generative_agents.config import WorkflowDefinition
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS
from generative_agents.persistence.models import (
    ExperimentWorkflow,
    ExperimentWorkflowVersion,
)
from generative_agents.services import ServiceError, WorkflowService
from generative_agents.web import create_app


def _llm_prompt_keys(workflow: dict) -> set[str]:
    return {
        node["prompt_key"]
        for node in workflow["nodes"]
        if node["kind"] == "llm"
    }


def test_new_experiment_has_five_revision_owned_workflows_and_default_versions(
    service, database
):
    created = service.create_experiment(
        name="Workflow defaults", source_type="BUILTIN_DEFAULT"
    )
    workflows = WorkflowService(database).list_workflows(created["id"])

    assert [item["workflow_key"] for item in workflows["items"]] == [
        "schedule",
        "memory",
        "action",
        "social",
        "reflection",
    ]
    assert all(item["version_count"] == 1 for item in workflows["items"])
    placed: set[str] = set()
    for item in workflows["items"]:
        detail = WorkflowService(database).get_workflow(
            created["id"], item["workflow_key"]
        )
        assert detail["versions"][0]["is_default"] is True
        assert detail["versions"][0]["version_no"] == 1
        created_at = detail["versions"][0]["created_at"]
        assert datetime.fromisoformat(created_at).utcoffset() is not None
        placed.update(_llm_prompt_keys(detail["workflow"]))
    assert placed == REQUIRED_PROMPT_KEYS


def test_workflow_save_is_isolated_versioned_and_optimistically_locked(service, database):
    first = service.create_experiment(name="Flow A", source_type="BUILTIN_DEFAULT")
    second = service.create_experiment(name="Flow B", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    first_flow = workflows.get_workflow(first["id"], "social")
    second_flow = workflows.get_workflow(second["id"], "social")
    changed = copy.deepcopy(first_flow["workflow"])
    changed["title"] = "社交与对话 · 实验 A"

    saved = workflows.save_workflow(
        first["id"],
        "social",
        expected_lock_version=first_flow["lock_version"],
        workflow=WorkflowDefinition.model_validate(changed),
        prompt_contents={"decide_chat": "只在存在明确共同话题时发起对话。"},
        label="收紧对话条件",
    )

    assert saved["lock_version"] == first_flow["lock_version"] + 1
    assert saved["workflow"]["title"].endswith("实验 A")
    assert saved["prompts"]["decide_chat"]["content"].startswith("只在")
    assert [item["version_no"] for item in saved["versions"]] == [2, 1]
    assert saved["versions"][0]["label"] == "收紧对话条件"
    unchanged = workflows.get_workflow(second["id"], "social")
    assert unchanged["workflow"] == second_flow["workflow"]
    assert unchanged["prompts"]["decide_chat"] == second_flow["prompts"]["decide_chat"]

    with pytest.raises(ServiceError) as exc:
        workflows.save_workflow(
            first["id"],
            "social",
            expected_lock_version=first_flow["lock_version"],
            workflow=WorkflowDefinition.model_validate(changed),
            prompt_contents={},
        )
    assert exc.value.code == "REVISION_CONFLICT"


def test_restore_default_workflow_restores_graph_and_its_prompt_contents(service, database):
    created = service.create_experiment(name="Restore flow", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    original = workflows.get_workflow(created["id"], "reflection")
    default_version = next(item for item in original["versions"] if item["is_default"])
    changed = copy.deepcopy(original["workflow"])
    changed["title"] = "被改错的反思流程"
    saved = workflows.save_workflow(
        created["id"],
        "reflection",
        expected_lock_version=original["lock_version"],
        workflow=WorkflowDefinition.model_validate(changed),
        prompt_contents={"reflect_focus": "错误正文"},
    )

    with pytest.raises(ServiceError) as conflict:
        workflows.restore_version(
            created["id"],
            "reflection",
            default_version["id"],
            expected_lock_version=original["lock_version"],
        )
    assert conflict.value.code == "REVISION_CONFLICT"

    restored = workflows.restore_version(
        created["id"],
        "reflection",
        default_version["id"],
        expected_lock_version=saved["lock_version"],
    )

    assert restored["workflow"] == original["workflow"]
    assert restored["prompts"]["reflect_focus"] == original["prompts"]["reflect_focus"]
    assert restored["restored_from_version_id"] == default_version["id"]
    assert restored["restored_as_version_no"] == 3
    assert restored["versions"][0]["label"] == "恢复默认流程"
    assert len(restored["versions"]) == 3


def test_restore_round_trip_removes_and_recreates_version_owned_custom_prompt(
    service, database
):
    created = service.create_experiment(
        name="Custom prompt restore", source_type="BUILTIN_DEFAULT"
    )
    workflows = WorkflowService(database)
    original = workflows.get_workflow(created["id"], "social")
    default_version = next(item for item in original["versions"] if item["is_default"])
    changed = copy.deepcopy(original["workflow"])
    end = next(node for node in changed["nodes"] if node["kind"] == "end")
    incoming = next(
        edge for edge in changed["edges"] if edge["target_node_id"] == end["node_id"]
    )
    source = next(
        node for node in changed["nodes"] if node["node_id"] == incoming["source_node_id"]
    )
    source_port = next(
        port for port in source["outputs"] if port["name"] == incoming["source_port"]
    )
    target_port = next(
        port for port in end["inputs"] if port["name"] == incoming["target_port"]
    )
    custom_key = "custom_social_policy"
    changed["nodes"].append(
        {
            "node_id": "custom_social_policy_node",
            "kind": "llm",
            "title": "Custom social policy",
            "inputs": [
                {
                    "name": "input",
                    "data_type": source_port["data_type"],
                    "required": True,
                    "description": "",
                }
            ],
            "outputs": [
                {
                    "name": "result",
                    "data_type": target_port["data_type"],
                    "required": True,
                    "description": "",
                }
            ],
            "position": {"x": 36, "y": max(0, end["position"]["y"] - 120)},
            "prompt_key": custom_key,
            "operation": None,
            "expression": None,
            "state_path": None,
            "subflow_key": None,
            "config": {},
        }
    )
    changed["edges"].remove(incoming)
    changed["edges"].extend(
        [
            {
                "source_node_id": source["node_id"],
                "source_port": incoming["source_port"],
                "target_node_id": "custom_social_policy_node",
                "target_port": "input",
                "branch": incoming["branch"],
                "case_value": incoming["case_value"],
            },
            {
                "source_node_id": "custom_social_policy_node",
                "source_port": "result",
                "target_node_id": end["node_id"],
                "target_port": incoming["target_port"],
                "branch": "always",
                "case_value": None,
            },
        ]
    )
    saved = workflows.save_workflow(
        created["id"],
        "social",
        expected_lock_version=original["lock_version"],
        workflow=WorkflowDefinition.model_validate(changed),
        prompt_contents={custom_key: "Use the custom social policy."},
        label="Custom policy",
    )
    custom_version = saved["versions"][0]

    restored_default = workflows.restore_version(
        created["id"],
        "social",
        default_version["id"],
        expected_lock_version=saved["lock_version"],
    )
    draft_after_default = service.get_draft(created["id"])
    assert custom_key not in draft_after_default["definition"]["prompts"]
    assert custom_key not in _llm_prompt_keys(restored_default["workflow"])

    restored_custom = workflows.restore_version(
        created["id"],
        "social",
        custom_version["id"],
        expected_lock_version=restored_default["lock_version"],
    )
    assert custom_key in _llm_prompt_keys(restored_custom["workflow"])
    assert restored_custom["prompts"][custom_key]["content"] == (
        "Use the custom social policy."
    )
    assert restored_custom["restored_as_version_no"] == 4
    assert restored_custom["versions"][0]["label"] == "恢复自版本 2"
    assert len(restored_custom["versions"]) == 4


def test_workflow_version_cannot_be_restored_across_experiments(service, database):
    first = service.create_experiment(name="Version owner A", source_type="BUILTIN_DEFAULT")
    second = service.create_experiment(name="Version owner B", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    first_detail = workflows.get_workflow(first["id"], "schedule")
    second_detail = workflows.get_workflow(second["id"], "schedule")

    with pytest.raises(ServiceError) as exc:
        workflows.restore_version(
            second["id"],
            "schedule",
            first_detail["versions"][0]["id"],
            expected_lock_version=second_detail["lock_version"],
        )
    assert exc.value.code == "WORKFLOW_VERSION_NOT_FOUND"


def test_unregistered_script_is_rejected_without_mutating_draft(service, database):
    created = service.create_experiment(name="Unsafe flow", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    current = workflows.get_workflow(created["id"], "memory")
    changed = copy.deepcopy(current["workflow"])
    script = next(node for node in changed["nodes"] if node["kind"] == "script")
    script["operation"] = "execute_arbitrary_python"

    with pytest.raises(ServiceError) as exc:
        workflows.save_workflow(
            created["id"],
            "memory",
            expected_lock_version=current["lock_version"],
            workflow=WorkflowDefinition.model_validate(changed),
            prompt_contents={},
        )
    assert exc.value.code == "WORKFLOW_SCRIPT_NOT_REGISTERED"
    assert workflows.get_workflow(created["id"], "memory")["lock_version"] == current[
        "lock_version"
    ]


def test_published_workflow_and_restore_versions_are_database_immutable(
    service, database, publishable_definition
):
    created = service.create_experiment(name="Immutable flow", source_type="BLANK")
    draft = service.get_draft(created["id"])
    payload = publishable_definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = created["experiment_key"]
    payload["experiment"]["name"] = created["name"]
    payload["experiment"]["goal"] = created["goal"]
    updated = service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=draft["lock_version"],
        definition=type(publishable_definition).model_validate(payload),
    )
    published = service.publish_draft(
        experiment_id=created["id"],
        draft_revision_id=updated["id"],
        expected_lock_version=updated["lock_version"],
    )

    with database.session_factory() as session:
        workflow_id = session.scalar(
            select(ExperimentWorkflow.id).where(
                ExperimentWorkflow.revision_id == published["id"]
            )
        )
        version_id = session.scalar(
            select(ExperimentWorkflowVersion.id).where(
                ExperimentWorkflowVersion.experiment_id == created["id"]
            )
        )
    with pytest.raises(IntegrityError, match="PUBLISHED_WORKFLOW_IMMUTABLE"):
        with database.session_factory.begin() as session:
            session.execute(
                update(ExperimentWorkflow)
                .where(ExperimentWorkflow.id == workflow_id)
                .values(workflow_hash="0" * 64)
            )
    with pytest.raises(IntegrityError, match="WORKFLOW_VERSION_IMMUTABLE"):
        with database.session_factory.begin() as session:
            session.execute(
                update(ExperimentWorkflowVersion)
                .where(ExperimentWorkflowVersion.id == version_id)
                .values(label="mutated")
            )


def test_workflow_http_contract_lists_saves_validates_and_restores(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={"name": "Workflow API", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        listing = client.get(
            f"/api/v1/experiments/{created['id']}/draft/workflows"
        )
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 5
        detail = client.get(
            f"/api/v1/experiments/{created['id']}/draft/workflows/action"
        ).json()
        workflow = detail["workflow"]
        workflow["title"] = "行动与空间 · API"
        saved = client.put(
            f"/api/v1/experiments/{created['id']}/draft/workflows/action",
            json={
                "lock_version": detail["lock_version"],
                "workflow": workflow,
                "prompts": {"determine_sector": "请选择最符合当前计划的区域。"},
                "label": "API 保存",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["workflow"]["title"].endswith("API")
        validation = client.post(
            f"/api/v1/experiments/{created['id']}/draft/workflows/action/validate"
        )
        assert validation.status_code == 200
        assert validation.json() == {
            "workflow_key": "action",
            "valid": True,
            "errors": [],
        }
        default_version = next(
            item for item in saved.json()["versions"] if item["is_default"]
        )
        restored = client.post(
            f"/api/v1/experiments/{created['id']}/draft/workflows/action/versions/{default_version['id']}/restore",
            json={"lock_version": saved.json()["lock_version"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["workflow"]["title"] == "行动与空间"
        assert restored.json()["restored_as_version_no"] == 3
        assert restored.json()["versions"][0]["label"] == "恢复默认流程"
