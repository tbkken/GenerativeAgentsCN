"""基础能力回归测试：覆盖 ``test_skill_platform`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_runtime.memory.stream import FileMemoryStream as MemoryStream
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_runtime.engine.iteration import IterationContext
from datetime import datetime, timezone
from uuid import uuid4
from generative_agents.ga_studio.resources.bundled import bundled_skills
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.skills.documents import SkillRegistryError
from generative_agents.ga_runtime.skills.executor import SkillLoopError
from generative_agents.ga_runtime.skills.executor import SkillRuntime
from tests.studio_support import create_test_studio


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
    registry = bundled_skills()

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
    registry = bundled_skills()
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
    registry = bundled_skills()
    runtime = ScriptedSkillRuntime(registry, responses=[{"content": "7"}])

    result = runtime.run("wake-up", "小明平日六点半起床。")

    start = result.trace[0]
    assert start["event"] == "skill.start"
    assert "只输出小时" in start["system_prompt"]
    assert start["user_prompt"] == "小明平日六点半起床。"


def test_run_trace_user_prompt_wraps_runtime_context():
    """回归验证 ``test_run_trace_user_prompt_wraps_runtime_context`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = bundled_skills()
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
    registry = bundled_skills()
    memory = MemoryStream(tmp_path / "memories", run_id=uuid4(), attempt_id=uuid4())
    now = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)
    memory.begin_step(1, now)
    iteration = IterationContext(run_id=uuid4(), attempt_id=uuid4(), agent_key="jane", agent_name="Jane", step_no=1, total_steps=2, now=now, stride_minutes=1, coord=(0, 0), address=("world",))
    mcp = SimulationMCPServer(None, iteration, memory_stream=memory)
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
    assert memory.search(agent_key="jane", query="咖啡馆")[0]["content"] == "简今天九点去咖啡馆上班"
    assert "简今天九点去咖啡馆上班" in runtime.requests[-1]["messages"][-1]["content"]
    tool_names = {tool["name"] for tool in mcp.tools()}
    assert {"memory-stream-supersede", "memory-stream-invalidate"} <= tool_names


def test_memory_stream_fallback_retrieves_chinese_semantics_without_spaces(tmp_path):
    memory = MemoryStream(tmp_path / "semantic-memory", run_id=uuid4(), attempt_id=uuid4())
    memory.begin_step(1, datetime(2026, 8, 28, tzinfo=timezone.utc))
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
    assert found[0]["retrieval_method"] == "file_lexical"


def test_skill_pack_can_call_its_private_script(tmp_path):
    root = tmp_path / "skills" / "atomic" / "text-helper"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: text-helper\ndescription: Normalize input text.\n---\nCall the private helper.", encoding="utf-8")
    (root / "scripts" / "text.py").write_text("def normalize(input_text, context):\n    return input_text.strip().upper()\n", encoding="utf-8")
    runtime = ScriptedSkillRuntime(SkillRegistry(tmp_path / "skills"), responses=[
        {"content": "", "tool_calls": [{"id": "script-1", "function": {"name": "run_skill_script", "arguments": json.dumps({"function": "text.normalize", "input_text": " cafe "})}}]},
        {"content": "CAFE"},
    ])
    result = runtime.run("text-helper", "Normalize text.")
    assert result.output_text == "CAFE"
    assert any(item["event"] == "script.call" for item in result.trace)
    assert "CAFE" in runtime.requests[-1]["messages"][-1]["content"]


def test_skill_api_starts_on_clean_database(tmp_path):
    """回归验证 ``test_skill_api_starts_on_clean_database`` 所描述的业务结果、故障边界和隔离约束。"""
    database_path = tmp_path / "app.db"
    app = create_test_studio(
        database_url=f"sqlite:///{database_path.as_posix()}",
        var_dir=str(tmp_path / "var"),
    )

    with TestClient(app) as client:
        catalog = client.get("/api/studio/resources/skills")
        created = client.post(
            "/api/studio/experiments",
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
    assert created.status_code == 422


def test_seeded_brain_can_be_deleted_and_is_not_recreated(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        brain = client.get("/api/studio/resources/skills/stanford-town-brain")
        assert brain.status_code == 200
        assert brain.json()["is_builtin"] is True
        deleted = client.delete("/api/studio/resources/skills/stanford-town-brain")
        assert deleted.status_code == 204, deleted.text

    restarted = create_test_studio(database_url=database_url)
    with TestClient(restarted) as client:
        missing = client.get("/api/studio/resources/skills/stanford-town-brain")
    assert missing.status_code == 404


def test_user_skill_is_mutable_in_database_and_never_written_to_source_tree(tmp_path):
    database_path = tmp_path / "app.db"
    app = create_test_studio(
        database_url=f"sqlite:///{database_path.as_posix()}",
        var_dir=str(tmp_path / "var"),
    )
    source_path = (
        Path(__file__).resolve().parents[2]
        / 'src' / 'generative_agents' / 'ga_studio' / 'bundled' / 'skills' / 'atomic' / 'user-runtime-skill' / 'SKILL.md'
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/studio/resources/skills",
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
            "/api/studio/resources/skills/user-runtime-skill",
            json={
                "markdown": markdown,
                "scripts": {
                    "scripts/main.py": (
                        "def run(input_text, context):\n"
                        "    return f'ACT: {input_text}'\n"
                    )
                },
            },
        )
        detail_with_script = client.get(
            "/api/studio/resources/skills/user-runtime-skill"
        ).json()
        found_by_display_name = client.get(
            "/api/studio/resources/skills?q=User%20Runtime%20Skill"
        ).json()["items"]
        found_by_key = client.get(
            "/api/studio/resources/skills?q=user-runtime-skill"
        ).json()["items"]
        history = client.get(
            "/api/studio/resources/skills/user-runtime-skill/history"
        ).json()["items"]
        archived = client.post(
            "/api/studio/resources/skills/user-runtime-skill/archive"
        )
        hidden = client.get("/api/studio/resources/skills/user-runtime-skill")
        visible_in_archive = client.get(
            "/api/studio/resources/skills?include_archived=true&q=user-runtime-skill"
        ).json()["items"]
        restored = client.post(
            "/api/studio/resources/skills/user-runtime-skill/restore"
        )
        deleted = client.delete("/api/studio/resources/skills/user-runtime-skill")
        missing_after_delete = client.get("/api/studio/resources/skills/user-runtime-skill")

    assert len(history) == 1
    assert first["storage"] == "database"
    assert first["path"].startswith("database://skills/")
    assert saved.status_code == 200
    assert saved.json()["content_hash"] != first["content_hash"]
    assert detail_with_script["script_sources"]["scripts/main.py"].startswith(
        "def run("
    )
    assert detail_with_script["scripts"] == ["scripts/main.py"]
    assert [item["name"] for item in found_by_display_name] == [
        "user-runtime-skill"
    ]
    assert [item["name"] for item in found_by_key] == ["user-runtime-skill"]
    assert archived.status_code == 200
    assert hidden.status_code == 404
    assert visible_in_archive[0]["archived_at"] is not None
    assert restored.status_code == 200
    assert deleted.status_code == 204
    assert missing_after_delete.status_code == 404
    assert source_path.exists() is False


def test_example_input_is_parsed_and_exposed(tmp_path):
    """回归验证 ``test_example_input_is_parsed_and_exposed`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry(root=tmp_path / "skills")
    skill_path = tmp_path / "skills" / "atomic" / "base-desc" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)

    markdown = (
        "---\n"
        "name: base-desc\n"
        "description: \"把角色事实整理成自然语言描述。\"\n"
        'example_input: "姓名：简\\n年龄：17岁\\n今天是 2026-08-19。简刚从床上醒来。"\n'
        "---\n\n"
        "# Base Desc\n"
    )
    skill_path.write_text(markdown, encoding="utf-8")
    document = registry.get("base-desc")

    detail = document.detail()
    expected = "姓名：简\n年龄：17岁\n今天是 2026-08-19。简刚从床上醒来。"
    assert detail["example_input"] == expected
    assert registry.get("base-desc").example_input == expected


def test_unknown_frontmatter_field_is_rejected(tmp_path):
    """回归验证 ``test_unknown_frontmatter_field_is_rejected`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry(root=tmp_path / "skills")
    skill_path = tmp_path / "skills" / "atomic" / "wake-up" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)

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
        skill_path.write_text(markdown, encoding="utf-8")
        registry.get("wake-up")


def test_builtin_skills_all_have_example_input():
    """回归验证 ``test_builtin_skills_all_have_example_input`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = bundled_skills()
    documents = registry.list()

    assert len(documents) >= 40
    missing = [item.name for item in documents if not item.example_input.strip()]
    assert missing == []


def test_repeated_identical_child_call_is_stopped_before_third_execution():
    registry = bundled_skills()
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


def test_semantically_repeated_child_call_with_paraphrased_input_is_stopped():
    registry = bundled_skills()

    def call_wake_up(call_id, input_text):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {
                        "name": "call_skill",
                        "arguments": json.dumps(
                            {"name": "wake-up", "input_text": input_text}
                        ),
                    },
                }
            ],
        }

    runtime = ScriptedSkillRuntime(
        registry,
        responses=[
            call_wake_up("first", "请判断几点起床"),
            {"content": "7"},
            call_wake_up("second", "换一种说法再判断起床时间"),
            {"content": "7"},
            call_wake_up("third", "最后再确认一次"),
        ],
        max_hops=12,
        max_identical_tool_calls=2,
    )

    with pytest.raises(SkillLoopError, match="semantic progress") as raised:
        runtime.run("daily-planning", "plan today")

    assert raised.value.trace[-1]["event"] == "loop.detected"
    assert raised.value.trace[-1]["tool"] == "call_skill"


def test_nested_skill_cannot_receive_world_act_and_mcp_errors_are_explicit():
    class _MCP:
        def tools(self):
            return [
                {
                    "name": "world-perceive",
                    "description": "read",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "world-act",
                    "description": "write",
                    "inputSchema": {"type": "object"},
                },
            ]

        def call(self, name, _arguments):
            assert name == "world-act"
            return {
                "content": [{"type": "text", "text": "MCP error: rejected"}],
                "isError": True,
            }

    registry = bundled_skills()
    runtime = ScriptedSkillRuntime(
        registry,
        mcp=_MCP(),
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "child",
                        "function": {
                            "name": "call_skill",
                            "arguments": json.dumps(
                                {
                                    "name": "action-and-space",
                                    "input_text": "propose an action",
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "候选 MOVE 参数"},
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "act",
                        "function": {
                            "name": "world-act",
                            "arguments": json.dumps({"action_type": "WAIT"}),
                        },
                    }
                ],
            },
            {"content": "已根据明确错误重新规划"},
        ],
    )

    result = runtime.run("stanford-town-brain", "drive one iteration")

    child_tools = {
        item["function"]["name"] for item in runtime.requests[1]["tools"]
    }
    assert "world-perceive" in child_tools
    assert "world-act" not in child_tools
    error_call = next(item for item in result.trace if item["event"] == "mcp.call")
    assert error_call["is_error"] is True
    assert json.loads(error_call["output_text"]) == {
        "isError": True,
        "error": "MCP error: rejected",
    }
    assert result.output_text == "已根据明确错误重新规划"


def test_successful_world_act_is_terminal_for_root_brain():
    class _MCP:
        def tools(self):
            return [
                {
                    "name": "world-act",
                    "description": "write",
                    "inputSchema": {"type": "object"},
                }
            ]

        def call(self, name, arguments):
            assert name == "world-act"
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"accepted": True, "action": arguments}
                        ),
                    }
                ],
                "isError": False,
            }

    runtime = ScriptedSkillRuntime(
        bundled_skills(),
        mcp=_MCP(),
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "final-action",
                        "function": {
                            "name": "world-act",
                            "arguments": json.dumps({"action_type": "WAIT"}),
                        },
                    }
                ],
            }
        ],
    )

    result = runtime.run("stanford-town-brain", "drive one iteration")

    assert result.output_text == "world-act accepted; this Agent iteration is complete."
    assert len(runtime.requests) == 1
    assert [item["event"] for item in result.trace[-2:]] == [
        "mcp.call",
        "skill.result",
    ]


def test_save_preserves_existing_example_input(tmp_path):
    """回归验证 ``test_save_preserves_existing_example_input`` 所描述的业务结果、故障边界和隔离约束。"""
    registry = SkillRegistry(root=tmp_path / "skills")
    skill_path = tmp_path / "skills" / "atomic" / "wake-up" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)

    first = (
        "---\n"
        "name: wake-up\n"
        "description: \"推断角色起床的小时。\"\n"
        'example_input: "agent：简\\nlifestyle：简通常早上7点起床，出门前吃个简餐。"\n'
        "---\n\n"
        "# Wake Up\n"
    )
    skill_path.write_text(first, encoding="utf-8")

    body = registry.get("wake-up").markdown
    second = body.replace("# Wake Up\n", "# Wake Up\n\n只输出一个 0-23 的小时数字。\n")
    skill_path.write_text(second, encoding="utf-8")
    document = registry.get("wake-up")

    assert document.example_input == "agent：简\nlifestyle：简通常早上7点起床，出门前吃个简餐。"
