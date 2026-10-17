"""Replay model-selected read calls against the real private-memory interface."""

from __future__ import annotations

import copy
import json
from collections import deque
from datetime import datetime, timezone
from uuid import UUID

import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.capabilities.server import SimulationAPI
from generative_agents.ga_runtime.engine.iteration import IterationContext
from generative_agents.ga_runtime.memory.stream import FileMemoryStream
from generative_agents.ga_runtime.skills.executor import SkillLoopError, SkillRuntime
from tests.skill_files import write_skill


def _tool(name, arguments, call_id):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


class _Responses(SkillRuntime):
    def __init__(self, *args, responses, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = deque(responses)
        self.requests = []

    def _complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools or []}))
        return self.responses.popleft()


class _RecordedAPI(SimulationAPI):
    def __init__(self, iteration, memory=None, *, game=None):
        super().__init__(game, iteration, memory_stream=memory)
        self.calls = []

    def invoke(self, name, arguments):
        result = super().invoke(name, arguments)
        self.calls.append(copy.deepcopy({"name": name, "arguments": arguments, "result": result}))
        return result


def _runtime(tmp_path, responses, *, memory_contents=(), max_hops=16):
    run_id = UUID("00000000-0000-4000-8000-000000000001")
    attempt_id = UUID("00000000-0000-4000-8000-000000000002")
    now = datetime(2024, 2, 13, 6, tzinfo=timezone.utc)
    memory = FileMemoryStream(tmp_path / "memory", run_id=run_id, attempt_id=attempt_id)
    memory.begin_step(1, now)
    for content in memory_contents:
        memory.append(agent_key="eddie", content=content)
    iteration = IterationContext(run_id=run_id, attempt_id=attempt_id,
        agent_key="eddie", agent_name="埃迪", step_no=1, total_steps=1, now=now,
        stride_minutes=10, coord=(1, 1), address=("World", "Home", "Room"))
    interfaces = _RecordedAPI(iteration, memory)
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "select-place", "选择活动片区", body=(
        "根据活动需求使用 @memory-stream-search 检索已知地点，必要时用 "
        "@memory-stream-append 保存新事实。候选穷尽后说明缺失信息并结束。"))
    runtime = _Responses(registry, responses=responses, interfaces=interfaces,
        max_hops=max_hops, max_identical_tool_calls=2, agent_key="eddie", step_no=1)
    return runtime, interfaces, memory


def test_distinct_searches_from_sector_trial_can_finish_with_no_known_destination(tmp_path):
    arguments = [
        {"query": "柳树市场和药店 商店 杂货店货架 地址", "limit": 5},
        {"query": "柳树市场 药店 商店 杂货店", "limit": 5},
        {"query": "柳树市场和药店", "limit": 10},
    ]
    runtime, interfaces, _ = _runtime(tmp_path, [
        *[_tool("memory-stream-search", args, f"search-{index}")
          for index, args in enumerate(arguments, 1)],
        {"content": "未检索到商店的已知地址，无法确认可达目的地。"},
    ])

    result = runtime.run("select-place", "为购买晚餐食材选择有真实依据的片区。")

    assert result.output_text == "未检索到商店的已知地址，无法确认可达目的地。"
    assert [call["arguments"] for call in interfaces.calls] == arguments
    assert all(call["result"] == {"ok": True, "result": []} for call in interfaces.calls)
    assert not any(event["event"] == "loop.detected" for event in result.trace)
    tool_messages = [message for message in runtime.requests[-1]["messages"] if message["role"] == "tool"]
    assert [(message["tool_call_id"], message["content"]) for message in tool_messages] == [
        ("search-1", "[]"), ("search-2", "[]"), ("search-3", "[]")]


def test_distinct_search_limits_can_return_the_same_known_place(tmp_path):
    runtime, interfaces, _ = _runtime(tmp_path, [
        *[_tool("memory-stream-search", {"query": "市场", "limit": limit}, f"search-{limit}")
          for limit in (1, 5, 10)],
        {"content": "只有一处已知市场。"},
    ], memory_contents=["市场位于已知街区。"])

    assert runtime.run("select-place", "核对已知市场。").output_text == "只有一处已知市场。"
    assert len(interfaces.calls) == 3
    assert interfaces.calls[0]["result"] == interfaces.calls[1]["result"] == interfaces.calls[2]["result"]
    assert len(interfaces.calls[0]["result"]["result"]) == 1


def test_identical_search_is_still_stopped_before_third_execution(tmp_path):
    runtime, interfaces, _ = _runtime(tmp_path, [
        _tool("memory-stream-search", {"query": "市场", "limit": 5}, f"search-{index}")
        for index in range(3)
    ])

    with pytest.raises(SkillLoopError, match="without progress") as caught:
        runtime.run("select-place", "重复查询同一市场。")

    assert len(interfaces.calls) == 2
    assert caught.value.trace[-1]["event"] == "loop.detected"
    assert caught.value.trace[-1]["repeat_count"] == 2


def test_memory_write_allows_same_search_against_new_state(tmp_path):
    search = {"query": "市场", "limit": 5}
    runtime, interfaces, _ = _runtime(tmp_path, [
        _tool("memory-stream-search", search, "search-1"),
        _tool("memory-stream-search", search, "search-2"),
        _tool("memory-stream-append", {"content": "市场位于已知街区。"}, "remember"),
        _tool("memory-stream-search", search, "search-3"),
        {"content": "已检索到刚记录的市场。"},
    ])

    assert runtime.run("select-place", "记录并核对市场。").output_text == "已检索到刚记录的市场。"
    assert interfaces.calls[0]["result"] == interfaces.calls[1]["result"] == {"ok": True, "result": []}
    assert interfaces.calls[-1]["result"]["result"][0]["content"] == "市场位于已知街区。"


def test_distinct_searches_remain_bounded_by_total_tool_budget(tmp_path):
    requests = [_tool("memory-stream-search", {"query": f"地点 {index}"}, f"search-{index}")
                for index in range(5)]
    runtime, interfaces, _ = _runtime(tmp_path, [{
        "content": "", "tool_calls": [response["tool_calls"][0] for response in requests],
    }], max_hops=4)

    with pytest.raises(SkillLoopError, match="configured 4 tool-call budget") as caught:
        runtime.run("select-place", "检查多个已知地点。")

    assert len(interfaces.calls) == 4
    assert caught.value.trace[-1]["event"] == "loop.budget_exhausted"


def test_nested_skills_share_the_read_call_budget(tmp_path):
    runtime, interfaces, _ = _runtime(tmp_path, [
        _tool("call_skill", {"name": "select-place", "input_text": "查询第一处。"}, "child-1"),
        _tool("memory-stream-search", {"query": "第一处"}, "search-1"),
        {"content": "第一处没有记忆。"},
        _tool("call_skill", {"name": "select-place", "input_text": "查询第二处。"}, "child-2"),
        _tool("memory-stream-search", {"query": "第二处"}, "search-2"),
    ], max_hops=3)
    write_skill(runtime.registry, "plan", "规划", kind="brain", body="调用 $select-place 查询目的地。")

    with pytest.raises(SkillLoopError, match="configured 3 tool-call budget") as caught:
        runtime.run("plan", "查找目的地。")

    assert len(interfaces.calls) == 1
    assert caught.value.trace[-1]["skill"] == "select-place"
    assert caught.value.trace[-1]["tool_call_count"] == 4


def test_different_unreachable_destinations_are_not_one_navigation_loop(tmp_path):
    from tests.runtime.test_visible_object_navigation import navigation_server

    server, _ = navigation_server(blocked_door=True)
    interfaces = _RecordedAPI(server.iteration, game=server.game)
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "check-route", "核验路线", body="使用 @world-navigate 核验候选目的地。")
    runtime = _Responses(registry, interfaces=interfaces, responses=[
        *[_tool("world-navigate", {"target_coord": coord}, f"navigate-{index}")
          for index, coord in enumerate(([5, 2], [6, 2], [5, 4]), 1)],
        {"content": "三个候选目的地均不可达。"},
    ])

    assert runtime.run("check-route", "核验三个候选目的地。").output_text == "三个候选目的地均不可达。"
    assert len(interfaces.calls) == 3
    assert all(call["result"] == {"ok": True, "result": {
        "reachable": False, "reason": "BLOCKED_OR_DISCONNECTED"}} for call in interfaces.calls)
