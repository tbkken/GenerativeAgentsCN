"""Malformed model output is a visible per-iteration degradation, not a Run stop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from generative_agents.ga_protocol.facts.quality import project_run_quality
from generative_agents.ga_runtime.engine.context import RunPaths
from generative_agents.ga_runtime.engine.scheduler import SimulationRunner
from generative_agents.ga_runtime.models.gateway import LLMModel
from generative_agents.ga_runtime.skills.brain import BrainRuntime
from generative_agents.ga_runtime.skills.executor import SkillModelError, SkillRuntimeError
from generative_agents.ga_runtime.storage.frames import FrameStore
from tests.runtime.test_object_skill_runtime import Committer, ToolModel, game_with_runtime
from tests.skill_files import write_skill


class _MalformedModel(LLMModel):
    def setup(self, config):
        self.calls = 0

    def _request_json(self, _method, _url, *, payload, **_kwargs):
        self.calls += 1
        # A valid private memory write precedes the invalid child invocation.
        # Repair attempts still see that tool result and keep returning bad output.
        wrote_memory = any(message.get("name") == "memory-stream-append"
                           for message in payload["messages"])
        name = "call_skill" if wrote_memory else "memory-stream-append"
        arguments = {} if wrote_memory else {"content": "uncommitted-format-attempt"}
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": f"call-{self.calls}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}}]}


class _PersistedCommitter(Committer):
    def __init__(self, game, root):
        super().__init__(game)
        self.frames = FrameStore(RunPaths(root, game.context.run_id))

    def commit(self, result, *, force_checkpoint):
        self.frames.write(result)
        return super().commit(result, force_checkpoint=force_checkpoint)


def _model():
    return _MalformedModel({"model": "offline", "base_url": "http://unused.invalid/v1",
                            "retry_attempts": 2, "retry_backoff_seconds": 0})


def _brain(game, model):
    registry = game.context.object_skill_runtime.registry
    write_skill(registry, "format-brain", "choose an action", kind="brain",
                body="Use @memory-stream-append and $helper, then @world-act.")
    write_skill(registry, "helper", "return advice", body="Return advice.")
    agent = game.agents["rider"]
    agent.profile.config = {}
    agent.spatial = SimpleNamespace(tree={}, address={})
    agent.chats = []
    runtime = BrainRuntime(registry, brain_skill="format-brain",
        model_config={"model": "offline", "retry_attempts": 2, "retry_backoff_seconds": 0},
        model_client=model, memory_stream=game.context.memory_stream)
    game.context.brain_runtime = runtime
    return runtime


def _report(game, root, step):
    return project_run_quality(root, run_id=str(game.context.run_id), committed_step=step,
        brain_skill="format-brain", evaluated_at="2026-10-17T12:00:00+08:00")


def test_format_failures_commit_safe_wait_past_four_steps_and_survive_new_attempt(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    model = _model()
    brain = _brain(game, model)
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)

    assert run.run(6, stride_minutes=1) == 6
    assert model.calls == 24  # one valid write, invalid output, then two repairs per Step
    assert game.context.memory_stream.search(agent_key="rider") == []
    before = _report(game, frames, 6)
    failures = [issue for issue in before["issues"] if issue["code"] == "BRAIN_RUNTIME_DEGRADED"]
    assert [item["step_no"] for item in failures] == list(range(1, 7))
    assert all(item["evidence"]["failure_kind"] == "model_output_format" for item in failures)
    assert all(item["evidence"]["fatal"] is False for item in failures)
    assert all(item["agent_key"] == "rider" for item in failures)
    assert brain._consecutive_fallbacks["rider"] == 0
    for result in commit.results:
        events = [event for event in result.domain_events if event.agent_keys == ("rider",)]
        assert len(events) == 1 and events[0].event_type == "AGENT_WAITED"
        assert "格式" in events[0].payload["structured_payload"]["description"]
        assert result.memory_deltas == ()

    # Recreate the Brain and restore the serialized world checkpoint for a new
    # Attempt. Quality is read from committed files, not the Brain's fresh cache.
    snapshot = json.loads(json.dumps(commit.snapshots[-1]))
    first_attempt = game.context.attempt_id
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(snapshot)
    game.context.clock.forward(1)
    resumed_model = _model()
    _brain(game, resumed_model)
    resumed = SimulationRunner(game.context, game, commit, completed_steps=6)
    assert resumed.run(5, stride_minutes=1) == 11
    after = _report(game, frames, 11)
    failures = [issue for issue in after["issues"] if issue["code"] == "BRAIN_RUNTIME_DEGRADED"]
    assert [item["step_no"] for item in failures] == list(range(1, 12))
    assert {item["source"]["attempt_id"] for item in failures} == {
        str(first_attempt), str(game.context.attempt_id)}
    assert game.context.memory_stream.search(agent_key="rider") == []


@pytest.mark.parametrize("action", [
    {"action_type": "WAIT", "wait_reason": "等待约定时间", "expected_until_step": 20},
    {"action_type": "ACT", "predicate": "睡觉", "object": "床"},
])
def test_repeated_valid_stationary_actions_never_count_as_failure(tmp_path, action):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    brain = _brain(game, ToolModel(lambda *_: ("world-act", action)))
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)

    assert run.run(6, stride_minutes=1) == 6
    assert brain._consecutive_fallbacks["rider"] == 0
    assert _report(game, frames, 6)["issues"] == []
    assert all(result.agents[0].from_coord == result.agents[0].to_coord for result in commit.results)


def test_repeated_infrastructure_failures_keep_the_existing_fatal_boundary(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))

    def unavailable(*_args):
        raise httpx.ConnectError("provider unavailable")

    _brain(game, ToolModel(unavailable))
    commit = Committer(game)
    run = SimulationRunner(game.context, game, commit)
    with pytest.raises(SkillModelError, match="provider unavailable"):
        run.run(6, stride_minutes=1)
    assert run.completed_steps == 3 and len(commit.results) == 3


def test_nonrecoverable_execution_error_still_aborts_without_a_wait_commit(tmp_path, monkeypatch):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    _brain(game, ToolModel(lambda *_: ("world-act", {"action_type": "WAIT"})))

    def broken(*_args, **_kwargs):
        raise SkillRuntimeError("execution invariant failed")

    monkeypatch.setattr("generative_agents.ga_runtime.skills.brain.SkillRuntime.run", broken)
    commit = Committer(game)
    run = SimulationRunner(game.context, game, commit)
    with pytest.raises(SkillRuntimeError, match="execution invariant failed"):
        run.run(1, stride_minutes=1)
    assert run.completed_steps == 0 and commit.results == []


def test_object_format_fallback_is_locatable_and_its_counter_restores(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    _brain(game, ToolModel(lambda *_: ("world-act", {"action_type": "WAIT"})))
    objects = game.context.object_skill_runtime
    write_skill(objects.registry, "facility", "observe", body="Use @memory-stream-append and $helper.")
    objects.model_client = _model()
    objects.model_config = {"model": "offline", "retry_attempts": 2, "retry_backoff_seconds": 0}
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)
    assert run.run(6, stride_minutes=1) == 6
    assert game.context.memory_stream.search(agent_key="game-object:camera") == []
    report = _report(game, frames, 6)
    failures = [issue for issue in report["issues"] if issue["code"] == "OBJECT_RUNTIME_DEGRADED"]
    assert len(failures) == 6
    assert all(item["object_key"] == "camera" and item["agent_key"] is None for item in failures)
    assert all(item["evidence"]["failure_kind"] == "model_output_format" for item in failures)
    assert game.game_object_interactions.object_state("camera")["state"] == "RED"
    assert game.consume_external_observations("rider") == ()
    for result in commit.results:
        events = [event for event in result.domain_events if event.event_type == "GAME_OBJECT_WAITED"]
        assert len(events) == 1
        assert "格式" in events[0].payload["structured_payload"]["description"]
        assert not result.memory_deltas
    snapshot = json.loads(json.dumps(commit.snapshots[-1]))
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(snapshot)
    assert game.game_object_interactions.runtime_state("camera")["consecutive_fallbacks"] == 6
    resumed = SimulationRunner(game.context, game, commit, completed_steps=6)
    assert resumed.run(1, stride_minutes=1) == 7
    assert game.game_object_interactions.runtime_state("camera")["consecutive_fallbacks"] == 7
