"""Exercise exact child-result handoff through the public Skill runtime."""

from __future__ import annotations

import copy
import json
from collections import deque

import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.models.retry import ModelCallCancelled
from generative_agents.ga_runtime.skills.executor import SkillLoopError, SkillModelError, SkillRuntime
from tests.skill_files import write_skill


def _call(name, call_id, **inputs):
    return {"content": "", "tool_calls": [{
        "id": call_id, "type": "function", "function": {
            "name": "call_skill",
            "arguments": json.dumps({"name": name, **inputs}, ensure_ascii=False),
        },
    }]}


class _Model:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []

    def chat_completion(self, messages, **kwargs):
        self.requests.append(copy.deepcopy({"messages": messages, **kwargs}))
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


def _runtime(tmp_path, responses, **kwargs):
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "parent", "组织任务", body="按需要调用 $source 和 $sink。")
    write_skill(registry, "source", "提供原文", body="必要时调用 $helper，然后返回完整原文。")
    write_skill(registry, "sink", "接收原文", body="直接处理收到的原文。")
    write_skill(registry, "helper", "辅助处理", body="返回处理结果。")
    model = _Model(responses)
    return SkillRuntime(registry, model_client=model, max_hops=20, **kwargs), model


def test_previous_child_output_reaches_next_child_without_rewriting(tmp_path):
    original = "  原文第一行。\n\n保留否定：尚未完成。\t\n"
    runtime, model = _runtime(tmp_path, [
        _call("source", "source-1", input_text="保留全文"),
        {"content": original},
        _call("sink", "sink-1", input_from="source-1"),
        {"content": "已处理。"},
        {"content": "已结束。"},
    ])

    result = runtime.run("parent", "转交结果。")

    assert len(model.requests) == 5
    assert model.requests[3]["messages"][-1]["content"] == original
    assert model.requests[2]["messages"][-1] == {
        "role": "tool", "tool_call_id": "source-1", "name": "call_skill", "content": original,
    }
    calls = [event for event in result.trace if event["event"] == "skill.call"]
    assert calls[1]["input_text"] == original
    assert calls[1]["input_from"] == "source-1"
    assert calls[1]["tool_call_id"] == "sink-1"


def test_explicit_empty_input_is_not_replaced_by_parent_task(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("sink", "empty", input_text=""),
        {"content": "处理空输入。"},
        {"content": "完成。"},
    ])

    runtime.run("parent", "This task must not replace an explicit empty child input.")

    assert model.requests[1]["messages"][-1]["content"] == ""


@pytest.mark.parametrize("inputs", [
    {},
    {"input_text": "文本", "input_from": "source-1"},
    {"input_text": None},
    {"input_text": 3},
    {"input_text": {"task": "文本"}},
    {"input_from": None},
    {"input_from": []},
    {"input_from": " "},
    {"input_from": "unknown"},
    {"input_text": "文本", "unexpected": "field"},
])
def test_invalid_child_input_is_rejected_before_target_execution(tmp_path, inputs):
    runtime, model = _runtime(tmp_path, [
        _call("sink", "invalid", **inputs),
        {"content": "不应执行。"},
        {"content": "不应完成。"},
    ])

    with pytest.raises(SkillModelError, match="call_skill"):
        runtime.run("parent", "原始任务。")

    assert len(model.requests) == 1


@pytest.mark.parametrize("call_id", [None, "", " ", 4, []])
def test_child_requires_an_unambiguous_call_id(tmp_path, call_id):
    response = _call("sink", call_id, input_text="文本")
    if call_id is None:
        del response["tool_calls"][0]["id"]
    runtime, model = _runtime(tmp_path, [response, {"content": "不应执行。"}, {"content": "结束。"}])

    with pytest.raises(SkillModelError, match="tool_call_id") as caught:
        runtime.run("parent", "执行任务。")

    assert len(model.requests) == 1
    assert caught.value.trace[-1]["event"] == "skill.call.rejected"
    assert caught.value.trace[-1]["child"] == "sink"


@pytest.mark.parametrize("same_message", [False, True])
def test_duplicate_call_ids_never_overwrite_a_result(tmp_path, same_message):
    first = _call("source", "duplicate", input_text="第一份")
    second = _call("sink", "duplicate", input_text="第二份")
    responses = [first, {"content": "第一份结果。"}, second,
                 {"content": "不应执行。"}, {"content": "结束。"}]
    if same_message:
        first["tool_calls"].extend(second["tool_calls"])
        responses = [first, {"content": "不应执行。"}, {"content": "不应执行。"}, {"content": "结束。"}]
    runtime, model = _runtime(tmp_path, responses)

    with pytest.raises(SkillModelError, match="duplicate tool_call_id") as caught:
        runtime.run("parent", "执行任务。")

    assert len(model.requests) == (1 if same_message else 3)
    assert caught.value.trace[-1]["event"] == "skill.call.rejected"


def test_repeated_skill_calls_can_be_selected_by_their_distinct_ids(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("source", "source-first", input_text="第一份"), {"content": "  第一份结果。\n"},
        _call("source", "source-second", input_text="第二份"), {"content": "\t第二份结果。\n"},
        _call("sink", "sink-first", input_from="source-first"), {"content": "已处理第一份。"},
        _call("sink", "sink-second", input_from="source-second"), {"content": "已处理第二份。"},
        {"content": "完成。"},
    ])

    runtime.run("parent", "依次处理两份资料。")

    assert model.requests[5]["messages"][-1]["content"] == "  第一份结果。\n"
    assert model.requests[7]["messages"][-1]["content"] == "\t第二份结果。\n"


def test_nested_child_result_is_not_visible_to_its_grandparent(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("source", "outer", input_text="生成结果"),
        _call("helper", "private-inner", input_text="内部资料"), {"content": "内部结果。"},
        {"content": "对外结果。"},
        _call("sink", "unavailable", input_from="private-inner"),
    ])

    with pytest.raises(SkillModelError, match="completed child result") as caught:
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == 5
    assert caught.value.trace[-1]["input_from"] == "private-inner"


def test_child_cannot_reference_its_parents_completed_result(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("source", "parent-result", input_text="第一份"), {"content": "父层资料。"},
        _call("source", "second-source", input_text="第二份"),
        _call("helper", "unavailable", input_from="parent-result"),
    ])

    with pytest.raises(SkillModelError, match="completed child result"):
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == 4


def test_result_references_do_not_survive_a_new_run_or_actor(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("source", "old-result", input_text="第一轮"), {"content": "第一轮资料。"},
        {"content": "第一轮完成。"},
        _call("sink", "new-call", input_from="old-result"),
    ], agent_key="alice")
    runtime.run("parent", "第一轮。")
    runtime.agent_key = "bob"

    with pytest.raises(SkillModelError, match="completed child result"):
        runtime.run("parent", "另一参与者的新一轮。")

    assert len(model.requests) == 4


def test_same_id_in_nested_callers_resolves_only_the_direct_child(tmp_path):
    runtime, model = _runtime(tmp_path, [
        _call("source", "same-id", input_text="生成结果"),
        _call("helper", "same-id", input_text="内部资料"), {"content": "内部结果。"},
        {"content": "对外结果。"},
        _call("sink", "sink", input_from="same-id"), {"content": "处理完成。"},
        {"content": "完成。"},
    ])

    runtime.run("parent", "处理任务。")

    assert model.requests[5]["messages"][-1]["content"] == "对外结果。"


def test_forward_reference_cannot_execute_an_unfinished_source(tmp_path):
    forward = _call("sink", "sink", input_from="future-source")
    forward["tool_calls"].extend(_call("source", "future-source", input_text="未来结果")["tool_calls"])
    runtime, model = _runtime(tmp_path, [forward])

    with pytest.raises(SkillModelError, match="completed child result"):
        runtime.run("parent", "先完成来源。")

    assert len(model.requests) == 1


def test_same_message_can_handoff_only_after_source_finishes(tmp_path):
    response = _call("source", "source", input_text="提供结果")
    response["tool_calls"].extend(_call("sink", "sink", input_from="source")["tool_calls"])
    runtime, model = _runtime(tmp_path, [response, {"content": "原文。\n"},
                                      {"content": "处理完成。"}, {"content": "完成。"}])

    runtime.run("parent", "按选择的顺序处理。")

    assert model.requests[2]["messages"][-1]["content"] == "原文。\n"


def test_different_references_to_identical_text_cannot_bypass_loop_detection(tmp_path):
    responses = []
    for index, name in enumerate(("source", "source-two", "source-three"), 1):
        responses.extend([_call(name, f"source-{index}", input_text="提供资料"), {"content": "同一份资料。"}])
    for index in range(1, 4):
        responses.append(_call("sink", f"sink-{index}", input_from=f"source-{index}"))
        if index < 3:
            responses.append({"content": "同一结果。"})
    runtime, model = _runtime(tmp_path, responses)
    for name in ("source-two", "source-three"):
        write_skill(runtime.registry, name, "提供资料", body="返回资料。")
    write_skill(runtime.registry, "parent", "组织任务",
                body="按需要调用 $source、$source-two、$source-three 与 $sink。")

    with pytest.raises(SkillLoopError, match="without progress") as caught:
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == 11
    assert caught.value.trace[-1]["event"] == "loop.detected"
    assert "semantic_scope" not in caught.value.trace[-1]


class _Capabilities:
    def __init__(self):
        self.calls = []

    def descriptors(self):
        return [{"name": "world-perceive", "description": "观察", "parameters": {"type": "object"}}]

    def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": True, "result": {"visible": True}}


def _read(call_id):
    response = _call("unused", call_id)
    response["tool_calls"][0]["function"] = {"name": "world-perceive", "arguments": "{}"}
    return response


@pytest.mark.parametrize("same_message", [False, True])
def test_tool_id_collision_between_interface_and_child_is_rejected_before_target(tmp_path, same_message):
    interfaces = _Capabilities()
    read = _read("shared-id")
    child = _call("sink", "shared-id", input_text="处理资料。")
    responses = [read, child, {"content": "不应执行。"}, {"content": "完成。"}]
    if same_message:
        read["tool_calls"].extend(child["tool_calls"])
        responses = [read, {"content": "不应执行。"}, {"content": "完成。"}]
    runtime, model = _runtime(tmp_path, responses, interfaces=interfaces)
    write_skill(runtime.registry, "parent", "组织任务", body="使用 @world-perceive 后调用 $sink。")

    with pytest.raises(SkillModelError, match="duplicate tool_call_id"):
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == (1 if same_message else 2)
    assert len(interfaces.calls) == (0 if same_message else 1)


def test_interface_result_is_not_a_child_result_reference(tmp_path):
    interfaces = _Capabilities()
    runtime, model = _runtime(tmp_path, [
        _read("read-1"), _call("sink", "sink", input_from="read-1"),
    ], interfaces=interfaces)
    write_skill(runtime.registry, "parent", "组织任务", body="使用 @world-perceive 后调用 $sink。")

    with pytest.raises(SkillModelError, match="completed child result"):
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == 2
    assert len(interfaces.calls) == 1


def test_cancelled_source_never_starts_the_waiting_target(tmp_path):
    response = _call("source", "source", input_text="提供结果")
    response["tool_calls"].extend(_call("sink", "sink", input_from="source")["tool_calls"])
    runtime, model = _runtime(tmp_path, [response, ModelCallCancelled("cancelled"),
                                      {"content": "不应执行。"}])

    with pytest.raises(ModelCallCancelled):
        runtime.run("parent", "处理任务。")

    assert len(model.requests) == 2
