"""Decision Skills retain model-call guarantees and compose with generative Skills."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.engine.context import RunControl
from generative_agents.ga_runtime.models.decision import DecisionModel
from generative_agents.ga_runtime.models.retry import ModelCallCancelled
from generative_agents.ga_runtime.models.trace import ModelTraceEventType, ModelTraceStatus
from generative_agents.ga_runtime.skills.executor import SkillRuntime, SkillRuntimeError


BODY = ("判断参与者是否准备完成；从 left（左侧出口）、right（右侧出口）中选择路线；"
        "按 later（稍后）、now（立即）评估紧急程度。仅使用传入的真实状态。")
QUESTIONS = {
    "ready": {"type": "noul", "instructions": BODY},
    "route": {"type": "choice", "instructions": BODY,
              "criteria": {"left": "left exit", "right": "right exit"}},
    "urgency": {"type": "score", "instructions": BODY, "criteria": ["later", "now"]},
}
ANSWERS = {
    "ready": {"type": "noul", "noul": 0.8},
    "route": {"type": "choice", "choice": "left", "confidence": 0.7,
              "probabilities": {"left": 0.9, "right": 0.1}},
    "urgency": {"type": "score", "score": 0.8, "confidence": 0.6,
                "legend": {"0": "later", "1": "now"}, "probabilities": {"0": 0.2, "1": 0.8}},
}


@pytest.fixture
def decision_provider():
    state = {"requests": [], "invalid": 0, "status": 200, "delay": 0,
             "entered": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            state["entered"].set()
            time.sleep(state["delay"])
            answers = copy.deepcopy(ANSWERS)
            if state["invalid"]:
                state["invalid"] -= 1
                answers.pop("ready")
            payload = json.dumps({"model": "laya-multilingual", "answers": answers,
                                  "usage": {"input_tokens": 28, "output_tokens": 0}}).encode()
            try:
                self.send_response(state["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state["config"] = {"provider": "unsloth", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                       "model": "laya-multilingual", "timeout_seconds": 2,
                       "retry_attempts": 3, "retry_backoff_seconds": 0}
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)


def registry(tmp_path):
    skill = tmp_path / "skills" / "pick-route" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: pick-route\ndescription: Classify the supplied facts\nllm_type: decision\n"
                     f"decision_questions: {json.dumps(QUESTIONS)}\n"
                     f"decision_prompt_sha256: {hashlib.sha256(BODY.encode('utf-8')).hexdigest()}\n---\n{BODY}",
                     encoding="utf-8")
    parent = tmp_path / "skills" / "plan-route" / "SKILL.md"
    parent.parent.mkdir(parents=True)
    parent.write_text("---\nname: plan-route\ndescription: Use route advice\n---\nCall $pick-route then explain.",
                      encoding="utf-8")
    return SkillRegistry(tmp_path)


def recorder():
    events = []
    return SimpleNamespace(run_id=uuid4(), attempt_id=uuid4(), append=events.append, events=events)


def test_decision_leaf_uses_systemone_without_chat_or_api_key(tmp_path, monkeypatch, decision_provider):
    chat_calls = []
    def forbidden_chat(*args, **kwargs):
        chat_calls.append((args, kwargs))
        raise AssertionError("compiled decision execution must not call a generative model")
    monkeypatch.setattr("generative_agents.ga_runtime.models.gateway.create_llm_model", forbidden_chat)
    audit = recorder()
    runtime = SkillRuntime(registry(tmp_path), decision_config=decision_provider["config"], recorder=audit)
    result = runtime.run("pick-route", "原始任务事实", context={"IterationContext": {"step_no": 7, "coord": [1, 2]}})
    assert json.loads(result.output_text) == ANSWERS
    request = decision_provider["requests"][0]
    assert request["path"] == "/v1/systemone"
    assert "Authorization" not in request["headers"]
    assert request["body"] == {"model": "laya-multilingual", "questions": QUESTIONS,
        "state": {"input_text": "原始任务事实", "IterationContext": {"step_no": 7, "coord": [1, 2]}}}
    logical = [event for event in audit.events if event.event_type == ModelTraceEventType.LOGICAL_END]
    assert len(logical) == 1
    physical = [event for event in audit.events if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT]
    assert physical[0].prompt_tokens == 28 and physical[0].completion_tokens == 0
    assert logical[0].provider == "unsloth"
    assert logical[0].prompt_key == "pick-route"
    assert runtime._model_client is None
    assert chat_calls == []
    assert result.trace[0]["decision_request"] == request["body"]


@pytest.mark.parametrize("change", ["missing_hash", "changed_body"])
def test_uncompiled_or_stale_decision_fails_before_any_model_call(tmp_path, change):
    copied = registry(tmp_path)
    source = copied.get("pick-route").path
    markdown = source.read_text(encoding="utf-8")
    if change == "missing_hash":
        markdown = "\n".join(line for line in markdown.splitlines()
                              if not line.startswith("decision_prompt_sha256:"))
    else:
        markdown += "\n新增约束：出口封闭时不得选择该出口。"
    source.write_text(markdown, encoding="utf-8")
    calls = []
    def unexpected_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid compilation must be rejected before model calls")
    runtime = SkillRuntime(copied,
        model_client=SimpleNamespace(chat_completion=unexpected_call),
        decision_client=SimpleNamespace(decision_completion=unexpected_call))
    with pytest.raises(SkillRuntimeError, match="Prompt"):
        runtime.run("pick-route", "真实状态")
    assert calls == []


def test_generative_parent_receives_decision_answers_and_continues(tmp_path, decision_provider):
    class Chat:
        def __init__(self):
            self.calls = []

        def chat_completion(self, messages, **_kwargs):
            self.calls.append(copy.deepcopy(messages))
            if len(self.calls) == 1:
                return {"content": "", "tool_calls": [{"id": "child", "type": "function", "function": {
                    "name": "call_skill", "arguments": json.dumps({"name": "pick-route", "input_text": "The left exit is open."})}}]}
            assert json.loads(messages[-1]["content"]) == ANSWERS
            return {"content": "Take the left exit."}

    chat = Chat()
    runtime = SkillRuntime(registry(tmp_path), model_client=chat, decision_config=decision_provider["config"])
    result = runtime.run("plan-route", "Find an exit")
    assert result.output_text == "Take the left exit."
    assert len(chat.calls) == 2 and len(decision_provider["requests"]) == 1
    assert decision_provider["requests"][0]["body"]["state"] == "The left exit is open."


def test_decision_missing_model_fails_explicitly(tmp_path):
    with pytest.raises(SkillRuntimeError, match="Decision model is not configured"):
        SkillRuntime(registry(tmp_path)).run("pick-route", "facts")


def test_incomplete_decisions_retry_with_one_logical_call(decision_provider):
    decision_provider["invalid"] = 1
    audit = recorder()
    model = DecisionModel(decision_provider["config"], recorder=audit)
    assert model.decision_completion("facts", QUESTIONS) == ANSWERS
    assert len(decision_provider["requests"]) == 2
    assert sum(event.event_type == ModelTraceEventType.LOGICAL_END for event in audit.events) == 1
    attempts = [event for event in audit.events if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT]
    assert [event.status for event in attempts] == [ModelTraceStatus.FAILED, ModelTraceStatus.SUCCEEDED]
    assert [(event.prompt_tokens, event.completion_tokens, event.total_tokens) for event in attempts] == [(28, 0, 28), (28, 0, 28)]


def test_invalid_question_never_sends_request(decision_provider):
    model = DecisionModel(decision_provider["config"])
    with pytest.raises(ValueError):
        model.decision_completion("facts", {"invalid": {"type": "chat"}})
    assert decision_provider["requests"] == []


def test_cancel_interrupts_decision_request_without_retry(decision_provider):
    decision_provider["delay"] = 1
    control = RunControl()
    model = DecisionModel(decision_provider["config"], control=control)
    def cancel_when_sent():
        assert decision_provider["entered"].wait(2)
        control.request_cancel()
    thread = threading.Thread(target=cancel_when_sent, daemon=True)
    thread.start()
    started = time.monotonic()
    with pytest.raises(ModelCallCancelled):
        model.decision_completion("facts", QUESTIONS)
    assert time.monotonic() - started < 0.8
    assert len(decision_provider["requests"]) == 1
    thread.join(timeout=1)


def test_decision_timeout_bounds_all_retries(decision_provider):
    decision_provider["delay"] = 0.8
    model = DecisionModel({**decision_provider["config"], "timeout_seconds": 0.15})
    started = time.monotonic()
    with pytest.raises(Exception, match="(?i)timed? ?out|timeout"):
        model.decision_completion("facts", QUESTIONS)
    assert time.monotonic() - started < 0.6
    assert len(decision_provider["requests"]) == 1


def test_embedded_decision_skill_drives_object_through_generative_root(tmp_path, monkeypatch, decision_provider):
    from generative_agents.ga_protocol.schemas.manifests import RunState
    from generative_agents.ga_replay.reader import ReplayReader
    from generative_agents.ga_runtime.lifecycle.executor import RuntimeModelRegistry, execute_run_directory
    from generative_agents.ga_runtime.lifecycle.package import load_experiment_directory
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
    from tests.runtime.test_object_skill_runtime import world
    from tests.test_portable_package_protocol import _definition

    definition = _definition()
    definition["world"]["definition"] = world()
    definition["models"]["decision"] = decision_provider["config"]
    sources = []
    docs = {
        "test-brain": ("brain", "Call @world-act."),
        "facility": ("skill", "Consult $pick-route then call @world-act to set the state."),
        "pick-route": ("skill", BODY),
    }
    for name, (kind, body) in docs.items():
        source = tmp_path / "author" / name / "SKILL.md"
        source.parent.mkdir(parents=True)
        extra = (f"llm_type: decision\ndecision_questions: {json.dumps(QUESTIONS)}\n"
                 f"decision_prompt_sha256: {hashlib.sha256(body.encode('utf-8')).hexdigest()}\n"
                 if name == "pick-route" else "")
        source.write_text(f"---\nname: {name}\ndescription: Test {name}\n{extra}---\n{body}", encoding="utf-8")
        sources.append(SkillSource(name, kind, source))
    package = ExperimentPackageBuilder().build_directory(
        tmp_path / "experiment", definition=definition, skills=sources,
        brain_skill="test-brain", object_roots=["facility"],
    )
    loaded = load_experiment_directory(package)
    assert loaded.definition.models.decision.model == "laya-multilingual"
    run_root = RunService().create(package, tmp_path / "run", requested_steps=1)
    sources[-1].skill_file.write_text("changed author content", encoding="utf-8")

    class Chat:
        calls = 0

        def chat_completion(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                name, arguments = "call_skill", {"name": "pick-route", "input_text": "The left exit is open."}
            else:
                assert json.loads(messages[-1]["content"]) == ANSWERS
                name, arguments = "world-act", {"action_type": "SET_OBJECT_STATE", "state_patch": {"state": "GREEN"}}
            return {"content": "", "tool_calls": [{"id": str(self.calls), "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    chat = Chat()
    original = RuntimeModelRegistry.get
    monkeypatch.setattr(RuntimeModelRegistry, "get", lambda self, purpose: chat if purpose == "chat" else original(self, purpose))
    status = execute_run_directory(run_root)
    assert status.status == RunState.COMPLETED and status.committed_step == 1
    assert chat.calls == 2
    assert decision_provider["requests"][0]["body"]["questions"] == QUESTIONS
    assert decision_provider["requests"][0]["body"]["state"]["IterationContext"]["game_object"]["object_key"] == "camera"
    with ReplayReader(run_root) as replay:
        assert replay.state_at(1)["object_states"]["camera"]["state"] == "GREEN"
