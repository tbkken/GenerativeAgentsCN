from __future__ import annotations

import copy
import random
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from generative_agents.config import (
    WorkflowDefinition,
    make_builtin_definition,
    make_default_workflows,
)
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS
from generative_agents.modules.prompt.scratch import Scratch
from generative_agents.persistence.models import (
    ExperimentWorkflow,
    ExperimentWorkflowVersion,
)
from generative_agents.services import ServiceError, WorkflowService
from generative_agents.runtime.json_schema import validate_json_schema
from generative_agents.runtime.workflow_functions import (
    invoke_inline_workflow_function,
    invoke_workflow_function,
    list_workflow_functions,
    validate_inline_workflow_function,
)
from generative_agents.web import create_app


def _llm_prompt_keys(workflow: dict) -> set[str]:
    return {
        node["prompt_key"]
        for node in workflow["nodes"]
        if node["kind"] == "llm"
    }


def test_schedule_default_is_a_real_prompt_router_with_strict_llm_contracts():
    schedule = make_default_workflows()["schedule"]
    by_id = {node.node_id: node for node in schedule.nodes}

    prompt_keys = {
        node.prompt_key for node in schedule.nodes if node.kind == "llm"
    }
    assert schedule.execution_mode == "prompt_router"
    assert by_id["route_prompt"].kind == "selector"
    assert by_id["route_prompt"].config["selector_mode"] == "case"
    assert {edge.case_value for edge in schedule.edges if edge.branch == "case"} == prompt_keys
    assert "Prompt 调用上下文" in by_id["start"].outputs[0].description
    assert by_id["prompt_schedule_revise"].prompt_key == "schedule_revise"
    for node in schedule.nodes:
        if node.kind != "llm":
            continue
        schema = node.config["response_schema"]
        assert schema["type"] == "object"
        assert schema["required"] == ["res"]
        assert node.config["retry_policy"] == {
            "max_attempts": 3,
            "retry_on_schema_error": True,
        }


def test_prompt_paths_support_nested_properties_and_legacy_dollar_variables():
    prompts = {"demo": "{step_context.agent.name} / ${label}"}
    scratch = Scratch(
        "测试 Agent",
        "",
        {},
        clock=SimpleNamespace(),
        random_source=random.Random(1),
        prompts=SimpleNamespace(get=prompts.__getitem__),
    )

    assert scratch.build_prompt(
        "demo",
        {"step_context": {"agent": {"name": "小林"}}, "label": "当前步骤"},
    ) == "小林 / 当前步骤"


def test_prompt_renderer_builds_structured_context_from_runtime_fields():
    prompts = {
        "demo": (
            "{context.agent.name} / {context.another.name} / "
            "{context.age} / {context.background} / {direct.value}"
        ),
        "published_compat": "{context} / {context.agent.name}",
    }
    scratch = Scratch(
        "测试 Agent",
        "",
        {},
        clock=SimpleNamespace(),
        random_source=random.Random(1),
        prompts=SimpleNamespace(get=prompts.__getitem__),
    )
    data = {
        "name": "小林",
        "another": "小周",
        "age": 28,
        "context": "近期记忆",
        "direct": {"value": "直接输入"},
    }

    assert scratch.build_prompt("demo", data) == "小林 / 小周 / 28 / 近期记忆 / 直接输入"
    assert scratch.build_prompt("published_compat", data) == "近期记忆 / 小林"


def test_all_bundled_prompts_render_from_runtime_style_flat_inputs():
    definition = make_builtin_definition(key="prompt-render", name="Prompt Render")
    variable_pattern = re.compile(
        r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}"
    )
    scratch = Scratch(
        "测试 Agent",
        "",
        {},
        clock=SimpleNamespace(),
        random_source=random.Random(1),
        prompts=SimpleNamespace(
            get=lambda key: definition.prompts[key].content,
        ),
    )

    def assign_path(data, path):
        parts = path.split(".")
        if parts[0] == "context":
            parts = parts[1:]
        if parts == ["agent", "name"]:
            data.setdefault("agent", "小林")
            return
        if parts == ["another", "name"]:
            data.setdefault("another", "小周")
            return
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target.setdefault(parts[-1], f"<{'.'.join(parts)}>")

    for prompt_key, prompt in definition.prompts.items():
        data = {}
        for path in variable_pattern.findall(prompt.content):
            assign_path(data, path)
        if prompt_key == "base_desc":
            data["name"] = data.pop("agent")["name"] if isinstance(data.get("agent"), dict) else data.pop("agent")
        rendered = scratch.build_prompt(prompt_key, data)
        assert not variable_pattern.findall(rendered), prompt_key


def test_bundled_llm_prompts_only_use_explicit_node_input_paths():
    definition = make_builtin_definition(key="explicit-prompts", name="Explicit Prompts")
    workflows = make_default_workflows()
    variable_pattern = re.compile(
        r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}"
    )

    base_desc = definition.prompts["base_desc"].content
    assert "{context.agent.name}" in base_desc
    assert "{context.daily_plan}" in base_desc
    assert "{name}" not in base_desc
    assert "${" not in base_desc

    for workflow in workflows.values():
        for node in workflow.nodes:
            if node.kind != "llm":
                continue
            roots = {port.name for port in node.inputs}
            content = definition.prompts[node.prompt_key].content
            assert "${" not in content
            assert {
                token.split(".", 1)[0]
                for token in variable_pattern.findall(content)
            } <= roots


def test_workflow_editor_does_not_offer_prompt_alias_migration():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "workflow-editor.js"
    ).read_text(encoding="utf-8")

    assert "旧变量别名" not in source
    assert "data-migrate-variables" not in source
    assert "migratePromptVariables" not in source


def test_workflow_nodes_do_not_render_a_nonfunctional_more_menu():
    root = Path(__file__).parents[2]
    source = (root / "generative_agents" / "web" / "static" / "workflow-editor.js").read_text(
        encoding="utf-8"
    )
    style = (root / "generative_agents" / "web" / "static" / "workflow-editor.css").read_text(
        encoding="utf-8"
    )

    assert "workflow-node-menu" not in source
    assert "workflow-node-menu" not in style
    assert "•••" not in source


def test_function_manager_groups_are_full_width_and_cards_use_the_inner_grid():
    root = Path(__file__).parents[2]
    style = (root / "generative_agents" / "web" / "static" / "console-ux.css").read_text(
        encoding="utf-8"
    )

    assert ".workflow-function-grid {\n  grid-template-columns: minmax(0, 1fr);" in style
    assert ".workflow-function-group-grid {\n  grid-template-columns: repeat(2, minmax(0, 1fr));" in style
    assert ".workflow-function-empty {\n  grid-column: 1 / -1;" in style
    assert "height: calc(100vh - var(--topbar-height) - 54px);" in style
    assert "overflow-y: auto;" in style
    assert "scrollbar-gutter: stable;" in style


def test_workflow_function_registry_is_visible_and_backed_by_callables():
    catalog = list_workflow_functions()
    schedule_function = next(item for item in catalog if item["key"] == "schedule_prepare_context")

    assert schedule_function["available"] is True
    assert schedule_function["implementation"].endswith(":schedule_prepare_context")
    assert "def schedule_prepare_context" in schedule_function["source"]
    assert schedule_function["scope"] == "system"
    assert schedule_function["editable"] is False
    assert invoke_workflow_function("normalize_list", {"input": "one"}) == {
        "result": ["one"]
    }
    prepared = invoke_workflow_function(
        "schedule_prepare_context",
        {"step_context": {"agent": {"name": "小林"}, "trigger": "new_day"}},
    )
    assert prepared["context"]["trigger"] == "new_day"


def test_inline_workflow_function_is_executable_but_rejects_ambient_python():
    source = """def main(inputs, context):
    values = inputs.get("values", [])
    return {"result": sum(values), "count": len(values)}
"""
    validate_inline_workflow_function(source)
    assert invoke_inline_workflow_function(source, {"values": [2, 3, 5]}) == {
        "result": 10,
        "count": 3,
    }
    with pytest.raises(ValueError, match="Import"):
        validate_inline_workflow_function(
            "def main(inputs, context):\n    import os\n    return {'result': 1}\n"
        )


def test_workflow_json_schema_rejects_malformed_structured_output():
    schema = {
        "type": "object",
        "properties": {"res": {"type": "integer", "minimum": 1, "maximum": 10}},
        "required": ["res"],
        "additionalProperties": False,
    }
    validate_json_schema({"res": 8}, schema)
    with pytest.raises(ValueError, match="at most 10"):
        validate_json_schema({"res": 11}, schema)


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
    script = next(node for node in changed["nodes"] if node["kind"] == "code")
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


def test_inline_script_source_is_saved_with_the_workflow_and_validated(service, database):
    created = service.create_experiment(name="Inline flow", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    current = workflows.get_workflow(created["id"], "memory")
    changed = copy.deepcopy(current["workflow"])
    script = next(node for node in changed["nodes"] if node["kind"] == "code")
    script["script_mode"] = "inline"
    script["script_source"] = (
        "def main(inputs, context):\n"
        "    return {'context': inputs.get('step_context', {})}\n"
    )
    saved = workflows.save_workflow(
        created["id"],
        "memory",
        expected_lock_version=current["lock_version"],
        workflow=WorkflowDefinition.model_validate(changed),
        prompt_contents={},
    )
    saved_script = next(
        node for node in saved["workflow"]["nodes"] if node["node_id"] == script["node_id"]
    )
    assert saved_script["script_mode"] == "inline"
    assert saved_script["script_source"] == script["script_source"]

    invalid = copy.deepcopy(saved["workflow"])
    invalid_script = next(node for node in invalid["nodes"] if node["kind"] == "code")
    invalid_script["script_source"] = (
        "def main(inputs, context):\n"
        "    import os\n"
        "    return {'context': os.getcwd()}\n"
    )
    with pytest.raises(ServiceError) as exc:
        workflows.save_workflow(
            created["id"],
            "memory",
            expected_lock_version=saved["lock_version"],
            workflow=WorkflowDefinition.model_validate(invalid),
            prompt_contents={},
        )
    assert exc.value.code == "WORKFLOW_INLINE_SCRIPT_INVALID"


def test_workflow_trial_run_executes_the_unsaved_graph_and_returns_node_trace(
    service, database
):
    created = service.create_experiment(name="Executable trial", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    current = workflows.get_workflow(created["id"], "memory")

    result = workflows.test_run_workflow(
        created["id"],
        "memory",
        workflow=WorkflowDefinition.model_validate(current["workflow"]),
        inputs={
            "step_context": {
                "trigger": "step",
                "agent": {"key": "trial", "name": "Trial"},
                "clock": "2000-01-01T00:00:00Z",
                "memories": [],
                "visible_events": [],
                "prompt_key": "poignancy_event",
                "prompt_request": {
                    "prompt": "trial",
                    "failsafe": None,
                    "retry": 1,
                },
            }
        },
        llm_outputs={
            "prompt_poignancy_event": 8,
            "prompt_poignancy_chat": 6,
        },
    )

    assert result["status"] == "SUCCEEDED"
    assert result["executed_nodes"][-1] == "end"
    assert {item["status"] for item in result["trace"]} == {"SUCCEEDED", "SKIPPED"}
    assert any(item["node_kind"] == "code" for item in result["trace"])
    assert any(item["node_kind"] == "llm" for item in result["trace"])
    assert any(
        item["node_id"] == "prompt_poignancy_chat"
        and item["status"] == "SKIPPED"
        for item in result["trace"]
    )


def test_legacy_draft_has_an_explicit_recoverable_prompt_router_migration(
    service, database
):
    created = service.create_experiment(name="Legacy router migration", source_type="BUILTIN_DEFAULT")
    workflows = WorkflowService(database)
    current = workflows.get_workflow(created["id"], "memory")
    legacy = copy.deepcopy(current["workflow"])
    legacy["execution_mode"] = "legacy_prompt_hook"
    saved = workflows.save_workflow(
        created["id"],
        "memory",
        expected_lock_version=current["lock_version"],
        workflow=WorkflowDefinition.model_validate(legacy),
        prompt_contents={},
        label="旧版图",
    )

    migrated = workflows.migrate_to_prompt_router(
        created["id"],
        "memory",
        expected_lock_version=saved["lock_version"],
    )

    assert migrated["workflow"]["execution_mode"] == "prompt_router"
    assert migrated["workflow"]["nodes"][2]["node_id"] == "route_prompt"
    assert migrated["versions"][0]["label"] == "迁移为真实 Prompt 路由"
    assert any(item["label"] == "旧版图" for item in migrated["versions"])


def test_publish_rejects_a_graph_that_is_structurally_valid_but_cannot_execute(
    service, database, publishable_definition
):
    created = service.create_experiment(name="Broken executable flow", source_type="BLANK")
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
    workflows = WorkflowService(database)
    current = workflows.get_workflow(created["id"], "memory")
    broken = copy.deepcopy(current["workflow"])
    code = next(node for node in broken["nodes"] if node["kind"] == "code")
    code["operation"] = None
    code["script_mode"] = "inline"
    code["script_source"] = (
        "def main(inputs, context):\n"
        "    return {'context': 1 / 0}\n"
    )
    saved = workflows.save_workflow(
        created["id"],
        "memory",
        expected_lock_version=updated["lock_version"],
        workflow=WorkflowDefinition.model_validate(broken),
        prompt_contents={},
    )

    with pytest.raises(ServiceError) as exc:
        service.publish_draft(
            experiment_id=created["id"],
            draft_revision_id=saved["revision_id"],
            expected_lock_version=saved["lock_version"],
        )

    assert exc.value.code == "CONFIG_VALIDATION_FAILED"
    assert any(
        item["code"] == "WORKFLOW_EXECUTION_FAILED"
        and item["path"].startswith("workflows.memory.prompt_")
        for item in exc.value.details["errors"]
    )


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

    workflow_service = WorkflowService(database)
    published_listing = workflow_service.list_revision_workflows(
        created["id"], published["id"]
    )
    assert published_listing["readonly"] is True
    assert published_listing["revision_id"] == published["id"]
    assert len(published_listing["items"]) == 5
    published_detail = workflow_service.get_revision_workflow(
        created["id"], published["id"], "schedule"
    )
    assert published_detail["readonly"] is True
    assert published_detail["workflow"]["workflow_key"] == "schedule"
    assert published_detail["prompts"]

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
        function_catalog = client.get("/api/v1/workflow-functions")
        assert function_catalog.status_code == 200
        assert any(
            item["key"] == "schedule_prepare_context" and item["available"]
            for item in function_catalog.json()["items"]
        )
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


def test_published_workflow_http_contract_is_readable_without_a_draft(
    database_url, publishable_definition
):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={"name": "Published workflow view", "source": {"type": "BLANK"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{created['id']}/draft").json()
        payload = publishable_definition.model_dump(mode="json", exclude_none=False)
        payload["experiment"]["key"] = created["experiment_key"]
        payload["experiment"]["name"] = created["name"]
        payload["experiment"]["goal"] = created["goal"]
        updated = app.state.experiment_service.update_draft(
            experiment_id=created["id"],
            expected_lock_version=draft["lock_version"],
            definition=type(publishable_definition).model_validate(payload),
        )
        published = app.state.experiment_service.publish_draft(
            experiment_id=created["id"],
            draft_revision_id=updated["id"],
            expected_lock_version=updated["lock_version"],
        )
        revision_id = published["id"]

        assert client.get(
            f"/api/v1/experiments/{created['id']}/draft/workflows"
        ).status_code == 409
        listing = client.get(
            f"/api/v1/experiments/{created['id']}/revisions/{revision_id}/workflows"
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["readonly"] is True
        assert len(listing.json()["items"]) == 5
        detail = client.get(
            f"/api/v1/experiments/{created['id']}/revisions/{revision_id}/workflows/action"
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["readonly"] is True
        assert detail.json()["workflow"]["workflow_key"] == "action"


def test_workflow_editor_uses_published_revision_as_a_readonly_data_source():
    root = Path(__file__).parents[2]
    editor = (
        root / "generative_agents" / "web" / "static" / "workflow-editor.js"
    ).read_text(encoding="utf-8")
    console = (
        root / "generative_agents" / "web" / "static" / "console-api.js"
    ).read_text(encoding="utf-8")

    assert "revision: state.revision" in console
    assert "function workflowApiRoot()" in editor
    assert "editorState.revision.id}/workflows" in editor
    assert "!editorState.ownerId || !editorState.revision" in editor
    assert "当前实验没有可编辑草稿" not in editor
    assert "revisionLabel" in editor and "· 只读" in editor
    assert "$('workflowValidateBtn').disabled = editorState.readonly" in editor
    assert "$('workflowFunctionCreateBtn').disabled = editorState.readonly" in editor
    assert "document.querySelectorAll('[data-connect-direction]')" in editor
    assert "function executionLocksRevision" in console
    assert "const workflowReadonly = executionLocksRevision() || !state.draft" in console
    assert "draft: workflowReadonly ? null : state.draft" in console
    assert "const effectiveRemoteDraft = lockedToPublished ? null : remoteDraft" in console


def test_custom_public_function_is_global_editable_and_reusable(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    source = """def main(inputs, context):
    value = inputs.get("input")
    return {"result": str(value).strip()}
"""
    with TestClient(app) as client:
        created_function = client.put(
            "/api/v1/workflow-functions/clean_text",
            json={
                "row_version": None,
                "function": {
                    "function_key": "clean_text",
                    "title": "清理文本",
                    "description": "全局复用的文本清理 Function",
                    "input_type": "string",
                    "output_type": "string",
                    "source": source,
                },
            },
        )
        assert created_function.status_code == 200, created_function.text
        custom = created_function.json()["item"]
        assert custom["scope"] == "custom"
        assert custom["editable"] is True
        assert custom["row_version"] == 1

        for name in ("Global Function A", "Global Function B"):
            experiment = client.post(
                "/api/v1/experiments",
                json={"name": name, "source": {"type": "BUILTIN_DEFAULT"}},
            ).json()
            detail = client.get(
                f"/api/v1/experiments/{experiment['id']}/draft/workflows/memory"
            ).json()
            script = next(
                node for node in detail["workflow"]["nodes"] if node["kind"] == "code"
            )
            script["script_mode"] = "shared"
            script["operation"] = "clean_text"
            saved = client.put(
                f"/api/v1/experiments/{experiment['id']}/draft/workflows/memory",
                json={
                    "lock_version": detail["lock_version"],
                    "workflow": detail["workflow"],
                    "prompts": {},
                },
            )
            assert saved.status_code == 200, saved.text

        updated_source = source.replace("strip()", "strip().lower()")
        updated = client.put(
            "/api/v1/workflow-functions/clean_text",
            json={
                "row_version": 1,
                "function": {
                    "function_key": "clean_text",
                    "title": "清理文本",
                    "description": "全局复用的文本清理 Function",
                    "input_type": "string",
                    "output_type": "string",
                    "source": updated_source,
                },
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["item"]["row_version"] == 2
        assert updated.json()["item"]["source"] == updated_source

        blocked_delete = client.request(
            "DELETE",
            "/api/v1/workflow-functions/clean_text",
            json={"row_version": 2},
        )
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["error"]["code"] == "WORKFLOW_FUNCTION_IN_USE"
