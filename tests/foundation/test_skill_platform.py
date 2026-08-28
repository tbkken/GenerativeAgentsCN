"""基础能力回归测试：覆盖 ``test_skill_platform`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generative_agents.skills import (
    MemoryStream,
    SkillMCPServer,
    SkillRegistry,
    SkillRegistryError,
    SkillLoopError,
    SkillRuntime,
)
from generative_agents.web.app import create_app


class ScriptedSkillRuntime(SkillRuntime):
    """为 ``ScriptedSkillRuntime`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self, *args, responses, **kwargs):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        super().__init__(*args, **kwargs)
        self.responses = deque(responses)
        self.requests = []

    def _complete(self, messages, *, tools=None):
        """为本测试模块封装 ``_complete`` 辅助步骤，减少重复的场景搭建代码。"""
        self.requests.append({"messages": messages, "tools": tools or []})
        return self.responses.popleft()


def test_file_backed_skill_catalog_and_brain_dependencies():
    """回归验证 ``test_file_backed_skill_catalog_and_brain_dependencies`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry()

    documents = registry.list()
    assert len(documents) >= 40
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
    assert "reflection-and-cognition" in crossing_brain.children
    assert "decide-game-object-response" not in crossing_brain.children


def test_skill_pack_hands_child_result_back_as_plain_text():
    """回归验证 ``test_skill_pack_hands_child_result_back_as_plain_text`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_run_trace_records_the_prompts_sent_to_the_model`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry()
    runtime = ScriptedSkillRuntime(registry, responses=[{"content": "7"}])

    result = runtime.run("wake-up", "小明平日六点半起床。")

    start = result.trace[0]
    assert start["event"] == "skill.start"
    assert "只输出小时" in start["system_prompt"]
    assert start["user_prompt"] == "小明平日六点半起床。"


def test_run_trace_user_prompt_wraps_runtime_context():
    """回归验证 ``test_run_trace_user_prompt_wraps_runtime_context`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_skill_pack_can_call_mcp_and_continue_with_natural_language`` 所描述的业务结果、故障边界和隔离约束。"""
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
    tool_names = {tool["name"] for tool in mcp.tools()}
    assert {"memory-stream-supersede", "memory-stream-invalidate"} <= tool_names


def test_memory_stream_fallback_retrieves_chinese_semantics_without_spaces(tmp_path):
    memory = MemoryStream(tmp_path / "semantic-memory.db")
    memory.append(
        agent_key="zhou",
        content="林晨告诉周宁，今天下午三点在咖啡水吧见面",
        subject="林晨",
        predicate="约周宁见面",
        object="下午三点咖啡水吧",
    )

    found = memory.search(
        agent_key="zhou", query="林晨约我什么时候在哪里见面？", limit=3
    )

    assert found and found[0]["object"] == "下午三点咖啡水吧"
    assert found[0]["retrieval_method"] == "hybrid_lexical"


def test_skill_pack_can_call_its_private_script(tmp_path):
    """回归验证 ``test_skill_pack_can_call_its_private_script`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_skill_api_starts_on_clean_database`` 所描述的业务结果、故障边界和隔离约束。"""
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
            },
        )

    assert catalog.status_code == 200
    counts = catalog.json()["counts"]
    assert counts["atomic"] >= 33
    assert counts["pack"] >= 5
    assert counts["brain"] >= 2
    assert runtime.json()["model"] == "qwen3.8:27b-q4_K_M"
    assert runtime.json()["handoff"] == "natural-language"
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_user_skill_is_database_versioned_and_never_written_to_source_tree(tmp_path):
    database_path = tmp_path / "app.db"
    app = create_app(
        database_url=f"sqlite:///{database_path.as_posix()}",
        var_dir=str(tmp_path / "var"),
        supervisor_enabled=False,
    )
    source_path = (
        Path(__file__).resolve().parents[2]
        / "generative_agents"
        / "data"
        / "skills"
        / "atomic"
        / "user-runtime-skill"
        / "SKILL.md"
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/skills",
            json={
                "name": "user-runtime-skill",
                "description": "用文本决定普通活动的 Event 语义。",
                "kind": "atomic",
            },
        )
        assert created.status_code == 201
        first = created.json()
        markdown = first["markdown"].replace(
            "说明如何完成任务",
            "使用 ACT 直接输出 Event(subject, predicate, object)",
        )
        saved = client.put(
            "/api/v1/skills/user-runtime-skill",
            json={"markdown": markdown},
        )
        history = client.get(
            "/api/v1/skills/user-runtime-skill/history"
        ).json()["items"]
        archived = client.post(
            "/api/v1/skills/user-runtime-skill/archive"
        )
        hidden = client.get("/api/v1/skills/user-runtime-skill")
        visible_in_archive = client.get(
            "/api/v1/skills?include_archived=true&q=user-runtime-skill"
        ).json()["items"]
        restored = client.post(
            "/api/v1/skills/user-runtime-skill/restore"
        )

    assert first["storage"] == "database"
    assert first["revision_no"] == 1
    assert first["path"].startswith("database://skills/")
    assert saved.status_code == 200
    assert saved.json()["revision_no"] == 2
    assert saved.json()["revision"] != first["revision"]
    assert [item["revision_no"] for item in history] == [2, 1]
    assert archived.status_code == 200
    assert hidden.status_code == 404
    assert visible_in_archive[0]["archived_at"] is not None
    assert restored.status_code == 200
    assert source_path.exists() is False


def test_example_input_is_parsed_and_exposed(tmp_path):
    """回归验证 ``test_example_input_is_parsed_and_exposed`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_unknown_frontmatter_field_is_rejected`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_builtin_skills_all_have_example_input`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry()
    documents = registry.list()

    assert len(documents) >= 40
    missing = [item.name for item in documents if not item.example_input.strip()]
    assert missing == []


def test_repeated_identical_child_call_is_stopped_before_third_execution():
    registry = SkillRegistry()
    call_wake_up = {
        "content": "",
        "tool_calls": [
            {
                "id": "repeat",
                "function": {
                    "name": "call_skill",
                    "arguments": json.dumps(
                        {"name": "wake-up", "input_text": "same input"}
                    ),
                },
            }
        ],
    }
    runtime = ScriptedSkillRuntime(
        registry,
        responses=[call_wake_up, {"content": "7"}, call_wake_up, {"content": "7"}, call_wake_up],
        max_hops=12,
        max_identical_tool_calls=2,
    )

    with pytest.raises(SkillLoopError, match="without progress"):
        runtime.run("daily-planning", "plan today")


def test_save_preserves_existing_example_input(tmp_path):
    """回归验证 ``test_save_preserves_existing_example_input`` 所描述的业务结果、故障边界和隔离约束。"""
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
