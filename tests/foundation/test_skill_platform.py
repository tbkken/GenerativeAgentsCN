from __future__ import annotations

import json
import sqlite3
from collections import deque
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.services import ExperimentService
from generative_agents.skills import (
    MemoryStream,
    SkillMCPServer,
    SkillRegistry,
    SkillRegistryError,
    SkillRuntime,
)
from generative_agents.web.app import create_app


class ScriptedSkillRuntime(SkillRuntime):
    def __init__(self, *args, responses, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = deque(responses)
        self.requests = []

    def _complete(self, messages, *, tools=None):
        self.requests.append({"messages": messages, "tools": tools or []})
        return self.responses.popleft()


def test_file_backed_skill_catalog_and_brain_dependencies():
    registry = SkillRegistry()

    documents = registry.list()
    assert len(documents) == 40
    assert {item.kind for item in documents} == {"atomic", "pack", "brain"}
    assert registry.get("wake_up").name == "wake-up"
    assert "只输出小时" in registry.prompt("wake_up")

    brain = registry.get("stanford-town-brain")
    assert brain.kind == "brain"
    assert set(brain.children) == {
        "daily-planning",
        "perception-and-memory",
        "action-and-space",
        "social-conversation",
        "reflection-and-cognition",
    }
    crossing_brain = registry.get("pedestrian-crossing-brain")
    assert crossing_brain.kind == "brain"
    assert "action-and-space" in crossing_brain.children
    assert "decide-game-object-response" in crossing_brain.children


def test_skill_pack_hands_child_result_back_as_plain_text():
    registry = SkillRegistry()
    runtime = ScriptedSkillRuntime(
        registry,
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "call_skill",
                            "arguments": json.dumps(
                                {"name": "wake-up", "input_text": "小明平日六点半起床。"},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {"content": "7"},
            {"content": "小明今天 7 点起床。"},
        ],
    )

    result = runtime.run("daily-planning", "为小明安排起床时间")

    assert result.output_text == "小明今天 7 点起床。"
    assert [item["event"] for item in result.trace] == [
        "skill.start",
        "skill.call",
        "skill.start",
        "skill.result",
        "skill.result",
    ]
    tool_result = runtime.requests[-1]["messages"][-1]
    assert tool_result["role"] == "tool"
    assert tool_result["content"] == "7"

    top_start = result.trace[0]
    assert "You may call one listed child Skill" in top_start["system_prompt"]
    assert top_start["user_prompt"] == "为小明安排起床时间"
    child_start = result.trace[2]
    assert "只输出小时" in child_start["system_prompt"]
    assert child_start["user_prompt"] == "小明平日六点半起床。"


def test_run_trace_records_the_prompts_sent_to_the_model():
    registry = SkillRegistry()
    runtime = ScriptedSkillRuntime(registry, responses=[{"content": "7"}])

    result = runtime.run("wake-up", "小明平日六点半起床。")

    start = result.trace[0]
    assert start["event"] == "skill.start"
    assert "只输出小时" in start["system_prompt"]
    assert start["user_prompt"] == "小明平日六点半起床。"


def test_run_trace_user_prompt_wraps_runtime_context():
    registry = SkillRegistry()
    runtime = ScriptedSkillRuntime(registry, responses=[{"content": "7"}])

    result = runtime.run(
        "wake-up", "小明平日六点半起床。", context={"agent_key": "xiaoming"}
    )

    start = result.trace[0]
    assert "Current task:" in start["user_prompt"]
    assert "小明平日六点半起床。" in start["user_prompt"]
    assert '"agent_key": "xiaoming"' in start["user_prompt"]


def test_skill_pack_can_call_mcp_and_continue_with_natural_language(tmp_path):
    registry = SkillRegistry()
    mcp = SkillMCPServer(MemoryStream(tmp_path / "memories.db"))
    runtime = ScriptedSkillRuntime(
        registry,
        mcp=mcp,
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "memory-1",
                        "function": {
                            "name": "memory-stream-append",
                            "arguments": json.dumps(
                                {
                                    "agent_key": "jane",
                                    "content": "简今天九点去咖啡馆上班",
                                    "poignancy": 5,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {"content": "这条工作记忆已经保存。"},
        ],
    )

    result = runtime.run("perception-and-memory", "记住简今天的安排")

    assert result.output_text == "这条工作记忆已经保存。"
    assert any(item["event"] == "mcp.call" for item in result.trace)
    assert mcp.memory.search(agent_key="jane", query="咖啡馆")[0]["content"] == "简今天九点去咖啡馆上班"
    assert "已写入 jane 的记忆流" in runtime.requests[-1]["messages"][-1]["content"]


def test_skill_pack_can_call_its_private_script(tmp_path):
    registry = SkillRegistry()
    runtime = ScriptedSkillRuntime(
        registry,
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "script-1",
                        "function": {
                            "name": "run_skill_script",
                            "arguments": json.dumps(
                                {
                                    "function": "memory_context.append_memory",
                                    "input_text": "Jane will work at the cafe at nine.",
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "The memory was persisted by the private Skill script."},
        ],
    )

    result = runtime.run(
        "perception-and-memory",
        "Remember Jane's work plan.",
        context={
            "agent_key": "jane",
            "memory_database": str(tmp_path / "private-script.db"),
            "kind": "plan",
            "poignancy": 4,
        },
    )

    assert result.output_text == "The memory was persisted by the private Skill script."
    assert any(item["event"] == "script.call" for item in result.trace)
    stored = MemoryStream(tmp_path / "private-script.db").search(
        agent_key="jane", query="cafe"
    )
    assert stored[0]["content"] == "Jane will work at the cafe at nine."
    tools = runtime.requests[0]["tools"]
    script_tool = next(
        item for item in tools if item["function"]["name"] == "run_skill_script"
    )
    assert script_tool["function"]["parameters"]["properties"]["function"]["enum"] == [
        "memory_context.append_memory",
        "memory_context.recall_memories",
    ]


def test_skill_api_starts_on_clean_database(tmp_path):
    database_path = tmp_path / "app.db"
    app = create_app(
        database_url=f"sqlite:///{database_path.as_posix()}",
        var_dir=str(tmp_path / "var"),
        supervisor_enabled=False,
    )

    with TestClient(app) as client:
        catalog = client.get("/api/v1/skills")
        runtime = client.get("/api/v1/skill-runtime")
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Skill E2E",
                "goal": "verify the cutover",
                "source": {"type": "BUILTIN_DEFAULT"},
            },
        )
        draft = client.get(f"/api/v1/experiments/{created.json()['id']}/draft")

    assert catalog.status_code == 200
    assert catalog.json()["counts"] == {"atomic": 33, "pack": 5, "brain": 2}
    assert runtime.json()["model"] == "qwen3.8:27b-q4_K_M"
    assert runtime.json()["handoff"] == "natural-language"
    assert created.status_code == 201
    assert "prompts" not in draft.json()["definition"]


def test_skill_cutover_upgrades_a_populated_legacy_database(tmp_path):
    database_path = tmp_path / "legacy-cutover.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url, "0022_scenario_templates")
    database = create_database(database_url)
    try:
        service = ExperimentService(database)
        experiment = service.create_experiment(
            name="Legacy cutover fixture", source_type="BLANK"
        )
        draft = service.get_draft(experiment["id"])
    finally:
        database.close()

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE experiment_revisions "
            "SET definition_json=json_set(definition_json, '$.prompts', json(?)) "
            "WHERE id=?",
            (json.dumps({"base_desc": {"content": "legacy"}}), draft["id"]),
        )
        connection.execute(
            "INSERT INTO scenario_templates "
            "(id,template_key,name,description,status,is_builtin,"
            "current_draft_revision_id,current_published_revision_id,row_version,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000101",
                "legacy-scenario",
                "Legacy scenario",
                "retired",
                "DRAFT",
                0,
                None,
                None,
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO scenario_template_revisions "
            "(id,template_id,revision_no,state,base_revision_id,schema_version,"
            "contract_json,contract_hash,validation_json,lock_version,created_at,updated_at,published_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "00000000-0000-0000-0000-000000000102",
                "00000000-0000-0000-0000-000000000101",
                1,
                "DRAFT",
                None,
                "legacy/v1",
                "{}",
                "0" * 64,
                None,
                1,
                now,
                now,
                None,
            ),
        )
        connection.execute(
            "UPDATE scenario_templates SET current_draft_revision_id=? WHERE id=?",
            (
                "00000000-0000-0000-0000-000000000102",
                "00000000-0000-0000-0000-000000000101",
            ),
        )
        connection.commit()

    upgrade_database(database_url)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        stored = connection.execute(
            "SELECT definition_json FROM experiment_revisions WHERE id=?",
            (draft["id"],),
        ).fetchone()[0]
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert version == "0024_step_effect_ledger"
    assert "scenario_templates" not in tables
    assert "scenario_template_revisions" not in tables
    assert "capability_definitions" not in tables
    definition = ExperimentDefinition.model_validate_json(stored)
    assert "prompts" not in definition.model_dump(mode="json")


def test_example_input_is_parsed_and_exposed(tmp_path):
    registry = SkillRegistry(root=tmp_path / "skills", history_root=tmp_path / "history")
    registry.create(name="base-desc", description="把角色事实整理成自然语言描述。")

    markdown = (
        "---\n"
        "name: base-desc\n"
        "description: \"把角色事实整理成自然语言描述。\"\n"
        'example_input: "姓名：简\\n年龄：17岁\\n今天是 2026-08-19。简刚从床上醒来。"\n'
        "---\n\n"
        "# Base Desc\n"
    )
    document = registry.save("base-desc", markdown)

    detail = document.detail()
    expected = "姓名：简\n年龄：17岁\n今天是 2026-08-19。简刚从床上醒来。"
    assert detail["example_input"] == expected
    assert registry.get("base-desc").example_input == expected


def test_unknown_frontmatter_field_is_rejected(tmp_path):
    registry = SkillRegistry(root=tmp_path / "skills", history_root=tmp_path / "history")
    registry.create(name="wake-up", description="推断角色起床的小时。")

    markdown = (
        "---\n"
        "name: wake-up\n"
        "description: \"推断角色起床的小时。\"\n"
        'example_input: "agent：简\\nlifestyle：简通常早上7点起床。"\n'
        "legacy: true\n"
        "---\n\n"
        "# Wake Up\n"
    )

    with pytest.raises(SkillRegistryError, match="Unsupported frontmatter field"):
        registry.save("wake-up", markdown)


def test_builtin_skills_all_have_example_input():
    registry = SkillRegistry()
    documents = registry.list()

    assert len(documents) == 40
    missing = [item.name for item in documents if not item.example_input.strip()]
    assert missing == []


def test_save_preserves_existing_example_input(tmp_path):
    registry = SkillRegistry(root=tmp_path / "skills", history_root=tmp_path / "history")
    registry.create(name="wake-up", description="推断角色起床的小时。")

    first = (
        "---\n"
        "name: wake-up\n"
        "description: \"推断角色起床的小时。\"\n"
        'example_input: "agent：简\\nlifestyle：简通常早上7点起床，出门前吃个简餐。"\n'
        "---\n\n"
        "# Wake Up\n"
    )
    registry.save("wake-up", first)

    body = registry.get("wake-up").markdown
    second = body.replace("# Wake Up\n", "# Wake Up\n\n只输出一个 0-23 的小时数字。\n")
    document = registry.save("wake-up", second)

    assert document.example_input == "agent：简\nlifestyle：简通常早上7点起床，出门前吃个简餐。"
