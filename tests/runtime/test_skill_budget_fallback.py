"""Execution budgets bound one iteration without turning valid progress into a Run failure."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from generative_agents.ga_runtime.engine.scheduler import SimulationRunner
from generative_agents.ga_runtime.skills.executor import SkillLoopError
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_runtime.skills.trial import execute_skill_trial
from generative_agents.ga_runtime.skills.trial_scene import build_trial_scene
from tests.runtime.test_brain_format_fallback import _PersistedCommitter, _brain, _report
from tests.runtime.test_object_skill_runtime import ToolModel, game_with_runtime
from tests.runtime.test_skill_trial_scene import trial


def _writing_model(*, finish_after=None):
    sequence = 0

    def choose(messages, _kwargs):
        nonlocal sequence
        completed = sum(message.get("name") == "memory-stream-append" for message in messages)
        if finish_after is not None and completed >= finish_after:
            return "world-act", {"action_type": "WAIT", "wait_reason": "已完成本轮准备，等待下一轮"}
        sequence += 1
        return "memory-stream-append", {"content": f"Progress item {sequence}"}

    return ToolModel(choose)


def _budget_failures(report, code):
    return [issue for issue in report["issues"]
            if issue["code"] == code and issue["evidence"].get("failure_kind") == "skill_budget"]


def test_brain_budget_exhaustion_is_a_per_step_fallback_across_attempts(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    brain = _brain(game, _writing_model())
    # A historical model-side value must not also control runtime call limits.
    brain.model_config["max_hops"] = 1
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)

    assert run.run(6, stride_minutes=1) == 6
    assert len(brain.model_client.calls) == 6 * 32
    assert brain._consecutive_fallbacks["rider"] == 0
    assert game.context.memory_stream.search(agent_key="rider") == []
    report = _report(game, frames, 6)
    failures = _budget_failures(report, "BRAIN_RUNTIME_DEGRADED")
    assert [item["step_no"] for item in failures] == list(range(1, 7))
    assert all(item["evidence"]["budget_kind"] == "skill_rounds" for item in failures)
    assert all(item["evidence"]["limit"] == item["evidence"]["used"] == 32 for item in failures)
    assert all(item["evidence"]["fatal"] is False for item in failures)
    budgets = [item for item in report["issues"] if item["code"] == "SKILL_EXECUTION_BUDGET_EXHAUSTED"]
    assert [item["step_no"] for item in budgets] == list(range(1, 7))
    assert all(item["evidence"]["budget_kind"] == "skill_rounds" for item in budgets)
    for result in commit.results:
        actions = [event for event in result.domain_events if event.agent_keys == ("rider",)]
        assert len(actions) == 1 and actions[0].event_type == "AGENT_WAITED"
        assert "预算" in actions[0].payload["structured_payload"]["description"]
        assert not result.memory_deltas

    first_attempt = game.context.attempt_id
    snapshot = json.loads(json.dumps(commit.snapshots[-1]))
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(snapshot)
    game.context.clock.forward(1)
    _brain(game, _writing_model())
    resumed = SimulationRunner(game.context, game, commit, completed_steps=6)
    assert resumed.run(5, stride_minutes=1) == 11
    failures = _budget_failures(_report(game, frames, 11), "BRAIN_RUNTIME_DEGRADED")
    assert [item["step_no"] for item in failures] == list(range(1, 12))
    assert {item["source"]["attempt_id"] for item in failures} == {
        str(first_attempt), str(game.context.attempt_id)}


def test_normal_long_iteration_is_not_bound_by_model_max_hops(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    brain = _brain(game, _writing_model(finish_after=15))
    brain.model_config["max_hops"] = 1
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)
    assert run.run(1, stride_minutes=1) == 1
    assert len(brain.model_client.calls) == 16
    assert len(commit.results[0].memory_deltas) == 15
    assert _report(game, frames, 1)["issues"] == []


def test_object_budget_is_locatable_and_checkpoint_counter_continues(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    _brain(game, ToolModel(lambda *_: ("world-act", {"action_type": "WAIT"})))
    objects = game.context.object_skill_runtime
    objects.model_client = _writing_model()
    objects.model_config["max_hops"] = 1
    frames = tmp_path / "run"
    commit = _PersistedCommitter(game, frames)
    run = SimulationRunner(game.context, game, commit)
    assert run.run(6, stride_minutes=1) == 6
    assert len(objects.model_client.calls) == 6 * 32
    assert game.context.memory_stream.search(agent_key="game-object:camera") == []
    report = _report(game, frames, 6)
    failures = _budget_failures(report, "OBJECT_RUNTIME_DEGRADED")
    assert len(failures) == 6
    assert all(item["object_key"] == "camera" and item["agent_key"] is None for item in failures)
    assert all(item["evidence"]["budget_kind"] == "skill_rounds" for item in failures)
    budgets = [item for item in report["issues"] if item["code"] == "SKILL_EXECUTION_BUDGET_EXHAUSTED"]
    assert len(budgets) == 6 and all(item["object_key"] == "camera" for item in budgets)
    snapshot = json.loads(json.dumps(commit.snapshots[-1]))
    game.context.attempt_id = uuid4()
    game.restore_runtime_state(snapshot)
    assert game.game_object_interactions.runtime_state("camera")["consecutive_fallbacks"] == 6
    resumed = SimulationRunner(game.context, game, commit, completed_steps=6)
    assert resumed.run(1, stride_minutes=1) == 7
    assert game.game_object_interactions.runtime_state("camera")["consecutive_fallbacks"] == 7


def test_real_repeated_reads_still_reach_the_existing_fatal_loop_boundary(tmp_path):
    game, _ = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    brain = _brain(game, ToolModel(lambda *_: ("memory-stream-search", {"query": "unchanged"})))
    document = brain.registry.get("format-brain")
    document.path.write_text(document.markdown + "\nUse @memory-stream-search.\n", encoding="utf-8")
    commit = _PersistedCommitter(game, tmp_path / "run")
    run = SimulationRunner(game.context, game, commit)
    with pytest.raises(SkillLoopError, match="without progress"):
        run.run(6, stride_minutes=1)
    assert run.completed_steps == 3 and len(commit.results) == 3


def test_trial_budget_uses_runtime_limits_and_reports_a_local_rolled_back_failure(tmp_path, monkeypatch):
    root = trial(tmp_path, body="Use @memory-stream-append to prepare notes.")
    model = _writing_model()
    scenes = []

    def capture_scene(*args, **kwargs):
        scene = build_trial_scene(*args, **kwargs)
        scenes.append(scene)
        return scene

    monkeypatch.setattr("generative_agents.ga_runtime.skills.trial.create_llm_model", lambda *a, **k: model)
    monkeypatch.setattr("generative_agents.ga_runtime.skills.trial.build_trial_scene", capture_scene)
    with pytest.raises(SkillTrialError) as caught:
        execute_skill_trial(root)
    assert caught.value.code == "SKILL_EXECUTION_BUDGET_EXHAUSTED"
    assert caught.value.status == 422
    assert len(model.calls) == 32
    budget = next(event for event in caught.value.trace if event["event"] == "budget.exhausted")
    assert budget["budget_kind"] == "skill_rounds" and budget["limit"] == budget["used"] == 32
    assert scenes[0].memory.search(agent_key="alice") == []
    assert scenes[0].interfaces.action is None
    assert not (root / "result.json").exists()
    assert not list(root.rglob("step-*.json.gz"))
