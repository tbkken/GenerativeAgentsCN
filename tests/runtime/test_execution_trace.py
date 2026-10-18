"""Live execution boundaries remain exact when large model payloads are disabled."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.engine.context import RunPaths
from generative_agents.ga_runtime.models.gateway import LLMModel
from generative_agents.ga_runtime.models.retry import ModelCallCancelled
from generative_agents.ga_runtime.models.trace import ModelTraceWriter
from generative_agents.ga_runtime.skills.executor import SkillRuntime, SkillRuntimeError
from generative_agents.ga_runtime.storage.execution import execution_span
from tests.skill_files import write_skill
from tests.runtime.test_object_skill_runtime import game_with_runtime, runner


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def recorder(tmp_path, *, run_id=None, attempt_id=None):
    run_id, attempt_id = run_id or uuid4(), attempt_id or uuid4()
    return ModelTraceWriter(RunPaths(tmp_path / "run", run_id), run_id=run_id,
                            attempt_id=attempt_id, attempt_no=1, capture_payloads=False)


def tool(name, args, call_id):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


class Model(LLMModel):
    def setup(self, config):
        self.responses = iter(config["responses"])

    def _chat_completion(self, *_args, **_kwargs):
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return result


class Interfaces:
    def __init__(self, writer, *, reject=False):
        self.writer, self.reject, self.calls = writer, reject, []

    def descriptors(self):
        return [{"name": name, "description": name, "parameters": {"type": "object"}}
                for name in ("memory-stream-append", "world-act")]

    def invoke(self, name, args):
        latest = records(self.writer.execution.path)[-1]
        assert (latest["kind"], latest["phase"], latest["status"]) == ("interface", "START", "RUNNING")
        self.calls.append((name, args))
        return {"ok": not self.reject, "result": {"id": "memory-one"}, "error": "denied"}


def test_nested_tree_and_actual_interfaces_do_not_need_payload_capture(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "root", "父大脑", kind="brain", body="$child @world-act")
    write_skill(registry, "child", "子技能", body="$leaf")
    write_skill(registry, "leaf", "叶子技能", body="@memory-stream-append")
    trace = recorder(tmp_path)
    api = Interfaces(trace)
    model = Model({"model": "test", "responses": [
        tool("call_skill", {"name": "child", "input_text": "private-task"}, "to-child"),
        tool("call_skill", {"name": "leaf", "input_text": "private-task"}, "to-leaf"),
        tool("memory-stream-append", {"content": "private-secret-input"}, "to-memory"),
        {"content": "private-secret-output"}, {"content": "child done"},
        tool("world-act", {"action_type": "WAIT"}, "to-world"),
    ]}, recorder=trace)
    runtime = SkillRuntime(registry, model_client=model, interfaces=api, recorder=trace,
                           agent_key="person", step_no=1)
    with execution_span(trace.execution, kind="step", name="Step 1", step_no=1):
        with execution_span(trace.execution, kind="actor", name="人物", agent_key="person",
                            actor_kind="agent", actor_index=1, actor_total=25):
            runtime.run("root", "private-task")
    events = records(trace.execution.path)
    starts = [event for event in events if event["phase"] == "START"]
    ends = {event["span_id"]: event for event in events if event["phase"] == "END"}
    skills = {event["skill_key"]: event for event in starts if event["kind"] == "skill"}
    assert skills["child"]["parent_span_id"] == skills["root"]["span_id"]
    assert skills["leaf"]["parent_span_id"] == skills["child"]["span_id"]
    assert skills["leaf"]["tool_call_id"] == "to-leaf"
    interfaces = [event for event in starts if event["kind"] == "interface"]
    assert interfaces[0]["parent_span_id"] == skills["leaf"]["span_id"]
    assert interfaces[0]["tool_call_id"] == "to-memory"
    assert interfaces[1]["parent_span_id"] == skills["root"]["span_id"]
    assert all(ends[event["span_id"]]["status"] == "SUCCEEDED" for event in starts)
    models = [event for event in starts if event["kind"] == "model"]
    logical = [event for event in records(trace.path) if event["event_type"] == "LOGICAL_END"]
    assert len(models) == len(logical) == 6
    assert {event["call_id"] for event in models} == {event["call_id"] for event in logical}
    assert all("payload" not in event for event in records(trace.path))
    text = trace.execution.path.read_text(encoding="utf-8")
    assert "private-secret" not in text and "private-task" not in text
    assert "COMMITTED" not in text
    assert all(event["actor_total"] == 25 for event in starts if event["kind"] != "step")


def test_interface_rejection_and_model_success_are_independent(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "leaf", "叶子技能", body="@memory-stream-append")
    trace = recorder(tmp_path)
    model = Model({"model": "test", "responses": [
        tool("memory-stream-append", {}, "denied"), {"content": "received rejection"},
    ]}, recorder=trace)
    SkillRuntime(registry, model_client=model, interfaces=Interfaces(trace, reject=True),
                 recorder=trace, step_no=1).run("leaf", "test")
    ends = [event for event in records(trace.execution.path) if event["phase"] == "END"]
    assert next(event for event in ends if event["kind"] == "interface")["status"] == "FAILED"
    assert all(event["status"] == "SUCCEEDED" for event in ends if event["kind"] == "model")


def test_cancel_closes_entire_nested_call_tree_and_restores_parent_scope(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    write_skill(registry, "root", "父大脑", kind="brain", body="$leaf")
    write_skill(registry, "leaf", "叶子技能")
    trace = recorder(tmp_path)
    model = Model({"model": "test", "responses": [
        tool("call_skill", {"name": "leaf", "input_text": "test"}, "child"), ModelCallCancelled("cancel"),
    ]}, recorder=trace)
    with pytest.raises(ModelCallCancelled):
        SkillRuntime(registry, model_client=model, recorder=trace, step_no=1).run("root", "test")
    events = records(trace.execution.path)
    starts = [event for event in events if event["phase"] == "START"]
    ends = {event["span_id"]: event for event in events if event["phase"] == "END"}
    assert len(starts) == len(ends)
    assert all(ends[event["span_id"]]["status"] == "CANCELLED"
               for event in starts if event["kind"] == "skill")
    assert [event["status"] for event in ends.values() if event["kind"] == "model"] == ["SUCCEEDED", "CANCELLED"]
    with execution_span(trace.execution, kind="step", name="independent", step_no=2):
        pass
    assert records(trace.execution.path)[-2]["parent_span_id"] is None


def test_actor_progress_includes_objects_and_step_cancel_has_explicit_rollback(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    trace = recorder(tmp_path, run_id=game.context.run_id, attempt_id=game.context.attempt_id)
    game.context.execution_trace = trace.execution
    game.context.object_skill_runtime.recorder = trace
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    events = records(trace.execution.path)
    actors = [event for event in events if event["kind"] == "actor" and event["phase"] == "START"]
    assert [event["actor_kind"] for event in actors] == ["agent", "object"]
    assert actors[-1]["agent_key"] == "game-object:camera"
    assert len(commit.results) == 1

    original = game.agent_think

    def cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        game.context.control.request_cancel()
        return result

    game.agent_think = cancel
    with pytest.raises(ModelCallCancelled):
        run.run(1, stride_minutes=1)
    cancelled = [event for event in records(trace.execution.path) if event["step_no"] == 2]
    assert cancelled[-2]["phase"] == "ROLLBACK"
    assert cancelled[-2]["kind"] == "step"
    assert cancelled[-1]["status"] == "CANCELLED"
    assert len(commit.results) == 1


@pytest.mark.parametrize("entrypoint", ["main", "helper"])
def test_invalid_script_result_fails_script_and_skill_spans(tmp_path, entrypoint):
    registry = SkillRegistry(tmp_path / "skills")
    document = write_skill(registry, "scripted", "脚本技能", body=f"[脚本](scripts/{entrypoint}.py)")
    scripts = document.path.parent / "scripts"
    scripts.mkdir()
    (scripts / f"{entrypoint}.py").write_text("def run(input_text, context):\n    return 42\n", encoding="utf-8")
    trace = recorder(tmp_path)
    responses = [] if entrypoint == "main" else [
        tool("run_skill_script", {"function": "helper.run", "input_text": "test"}, "script-call")]
    model = Model({"model": "test", "responses": responses}, recorder=trace)
    with pytest.raises(SkillRuntimeError, match="expected str"):
        SkillRuntime(registry, model_client=model, recorder=trace).run("scripted", "test")
    ends = [event for event in records(trace.execution.path) if event["phase"] == "END"]
    assert all(event["status"] == "FAILED" for event in ends if event["kind"] in {"script", "skill"})
    assert len([event for event in ends if event["kind"] == "script"]) == 1


def test_successful_interface_is_marked_rolled_back_when_actor_falls_back(tmp_path):
    calls = []

    def choose(*_args):
        calls.append(True)
        if len(calls) == 1:
            return "memory-stream-append", {"content": "will be rolled back"}
        raise RuntimeError("model failed after memory write")

    game, _ = game_with_runtime(tmp_path, choose, agents=False)
    trace = recorder(tmp_path, run_id=game.context.run_id, attempt_id=game.context.attempt_id)
    game.context.execution_trace = trace.execution
    game.context.object_skill_runtime.recorder = trace
    run, commit = runner(game)
    run.run(1, stride_minutes=1)
    events = records(trace.execution.path)
    interface = next(event for event in events if event["kind"] == "interface" and event["phase"] == "END")
    assert interface["status"] == "SUCCEEDED"
    rollback = next(event for event in events if event["phase"] == "ROLLBACK")
    assert rollback["kind"] == "actor" and rollback["status"] == "FAILED"
    skill = next(event for event in events if event["span_id"] == interface["parent_span_id"])
    assert skill["parent_span_id"] == rollback["span_id"]
    assert json.loads(game.context.memory_stream.path.read_text(encoding="utf-8"))["items"] == []
    assert len(commit.results) == 1
