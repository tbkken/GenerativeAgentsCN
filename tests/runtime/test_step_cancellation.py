"""Cancellation stops new work and preserves only fully committed Step facts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock
from types import SimpleNamespace

import pytest

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_protocol.schemas.manifests import RunState
from generative_agents.ga_runtime.lifecycle.control import FileRunControl
from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_runtime.skills.executor import SkillRuntime
from tests.fixtures.skills_registry import fixture_skills
from tests.runtime.test_object_skill_runtime import Rider, game_with_runtime, runner
from tests.test_portable_package_protocol import _experiment


def test_cancel_between_agents_discards_whole_step_and_prior_agent_memory(tmp_path):
    game, model = game_with_runtime(tmp_path, lambda *_: ("world-act", {"action_type": "WAIT"}))
    game.agents["second"] = Rider(game.maze, "second")
    game.agent_keys_by_name["second"] = "second"
    game.agents["third"] = Rider(game.maze, "third")
    game.agent_keys_by_name["third"] = "third"
    run, commit = runner(game)
    original_think = game.agent_think
    memory = game.context.memory_stream
    seen = []
    memory.begin_step(1, game.context.clock.get_date())
    previous = memory.append(agent_key="rider", content="此前已提交的记忆")
    memory.drain_result_events()
    committed_memory = read_json(memory.path)
    run.completed_steps = 1

    def think(key, status, **kwargs):
        seen.append(key)
        memory.append(agent_key=key, content="本步尚未提交的记忆")
        result = original_think(key, status, **kwargs)
        if key == "second":
            game.context.control.request_cancel()
        return result

    game.agent_think = think
    with pytest.raises(RuntimeError, match="cancel"):
        run.run(1, stride_minutes=1)
    assert seen == ["rider", "second"]
    assert model.calls == []
    assert commit.results == []
    assert run.completed_steps == 1
    assert read_json(memory.path) == committed_memory
    assert read_json(memory.path)["items"][0]["id"] == previous["id"]
    assert memory.drain_result_events() == ()


def test_cancel_during_object_response_does_not_commit_object_or_agent_memory(tmp_path):
    def choose(*_):
        game.context.control.request_cancel()
        return "world-act", {"action_type": "SET_OBJECT_STATE", "state_patch": {"state": "GREEN"}}

    game, model = game_with_runtime(tmp_path, choose)
    game.context.object_skill_runtime.control = game.context.control
    run, commit = runner(game)
    original_think = game.agent_think
    memory = game.context.memory_stream

    def think(key, status, **kwargs):
        memory.append(agent_key=key, content="来自前一位参与者但尚未提交")
        return original_think(key, status, **kwargs)

    game.agent_think = think
    with pytest.raises(RuntimeError, match="cancel"):
        run.run(1, stride_minutes=1)
    assert len(model.calls) == 1
    assert game.game_object_interactions.object_state("camera")["state"] == "RED"
    assert commit.results == []
    assert read_json(memory.path)["items"] == []


def test_skill_preserves_cancellation_instead_of_recoverable_model_failure():
    from generative_agents.ga_runtime.models.retry import ModelCallCancelled

    def cancel(*_args, **_kwargs):
        raise ModelCallCancelled("cancel requested")

    runtime = SkillRuntime(fixture_skills(), model_client=SimpleNamespace(chat_completion=cancel))
    with pytest.raises(ModelCallCancelled):
        runtime.run("wake-up", "起床")


@pytest.mark.parametrize("state", [RunState.CREATED, RunState.QUEUED])
def test_cancel_before_worker_start_is_not_reset_or_given_an_attempt(tmp_path, state):
    package = _experiment(tmp_path)
    run_root = RunService().create(package, tmp_path / "run")
    status = read_json(run_root / "status.json")
    status["status"] = state.value
    atomic_write_json(run_root / "status.json", status)
    FileRunControl(run_root).request_cancel()

    result = execute_run_directory(run_root)

    assert result.status is RunState.CANCELLED
    assert result.committed_step == 0
    assert list((run_root / "attempts").iterdir()) == []


@pytest.mark.parametrize("initial", [RunState.CREATED, RunState.PAUSED])
def test_cancel_during_package_loading_does_not_create_an_attempt(tmp_path, monkeypatch, initial):
    package = _experiment(tmp_path)
    run_root = RunService().create(package, tmp_path / "run")
    status = read_json(run_root / "status.json")
    status["status"] = initial.value
    atomic_write_json(run_root / "status.json", status)
    import generative_agents.ga_runtime.lifecycle.executor as executor
    original_load = executor.load_experiment_directory

    def load_and_cancel(path):
        loaded = original_load(path)
        FileRunControl(run_root).request_cancel()
        return loaded

    monkeypatch.setattr(executor, "load_experiment_directory", load_and_cancel)
    result = execute_run_directory(run_root)

    assert result.status is RunState.CANCELLED
    assert result.committed_step == 0
    attempts = list((run_root / "attempts").glob("*/attempt.json"))
    assert attempts == []
    assert not list((run_root / "frames").glob("step-*"))


def test_cancel_during_step_finishes_attempt_cancelled_instead_of_failed(tmp_path, monkeypatch):
    from generative_agents.ga_runtime.models.retry import ModelCallCancelled
    import generative_agents.ga_runtime.lifecycle.executor as executor

    package = _experiment(tmp_path)
    run_root = RunService().create(package, tmp_path / "run")

    def build_cancelled_runner(context, *_args, **_kwargs):
        def run(*_args, **_kwargs):
            context.control.request_cancel()
            raise ModelCallCancelled("cancel requested")
        return SimpleNamespace(run=run, committer=None, completed_steps=0)

    monkeypatch.setattr(executor, "build_runner", build_cancelled_runner)
    result = execute_run_directory(run_root)

    assert result.status is RunState.CANCELLED
    assert result.committed_step == 0
    attempt = next((run_root / "attempts").glob("*/attempt.json"))
    assert read_json(attempt)["status"] == "CANCELLED"
    assert not list((run_root / "frames").glob("step-*"))


def test_cancel_during_model_discovery_finishes_attempt_cancelled(tmp_path, monkeypatch):
    from generative_agents.ga_runtime.models.retry import check_cancelled
    from generative_agents.ga_runtime.models.gateway import VLLMLLMModel
    import generative_agents.ga_runtime.lifecycle.executor as executor

    package = _experiment(tmp_path)
    run_root = RunService().create(package, tmp_path / "run")
    original_get = executor.RuntimeModelRegistry.get
    requests = []

    def discover_model(self, purpose):
        self.config["model"] = "auto"
        return original_get(self, purpose)

    def cancel_discovery(self, method, url, **_kwargs):
        requests.append((method, url))
        assert method == "GET" and url.endswith("/models")
        self._control.request_cancel()
        check_cancelled(self._control)
        pytest.fail("model discovery must propagate cancellation")

    monkeypatch.setattr(executor.RuntimeModelRegistry, "get", discover_model)
    monkeypatch.setattr(VLLMLLMModel, "_request_json", cancel_discovery)
    result = execute_run_directory(run_root)

    assert len(requests) == 1
    assert result.status is RunState.CANCELLED
    assert result.committed_step == 0
    attempt = next((run_root / "attempts").glob("*/attempt.json"))
    assert read_json(attempt)["status"] == "CANCELLED"
    assert read_json(attempt)["failure"] is None
    assert not list((run_root / "frames").glob("step-*"))


@pytest.mark.parametrize("initial", [RunState.CREATED, RunState.PAUSED])
def test_submit_preserves_new_cancel_and_resets_only_explicit_resume_controls(tmp_path, monkeypatch, initial):
    import generative_agents.ga_runtime.supervision.processes as processes

    package = _experiment(tmp_path)
    run_root = RunService().create(package, tmp_path / "run")
    status = read_json(run_root / "status.json")
    status["status"] = initial.value
    atomic_write_json(run_root / "status.json", status)
    FileRunControl(run_root).request_cancel()
    observed = []

    def start_worker(*_args, **_kwargs):
        control = FileRunControl(run_root)
        observed.append(control.cancel_requested)
        assert read_json(run_root / "status.json")["status"] == "QUEUED"
        control.request_cancel()
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(processes.subprocess, "Popen", start_worker)
    monkeypatch.setattr(processes.threading, "Thread", lambda **_: SimpleNamespace(start=lambda: None))
    processes.FileRunSupervisor().submit(run_root)
    result = execute_run_directory(run_root)

    assert observed == [initial is RunState.CREATED]
    assert result.status is RunState.CANCELLED
    assert list((run_root / "attempts").iterdir()) == []


def test_concurrent_pause_and_cancel_preserve_both_file_control_flags(tmp_path, monkeypatch):
    import generative_agents.ga_runtime.lifecycle.control as control_module

    pause_control = FileRunControl(tmp_path)
    cancel_control = FileRunControl(tmp_path)
    readers = Barrier(2)
    replacements = Lock()
    original_write = control_module.atomic_write_json

    def serialized_replace(path, value):
        # Isolate read-modify-write loss from platform-specific simultaneous
        # replace errors. Serializing only replacement cannot fix stale reads.
        with replacements:
            original_write(path, value)

    monkeypatch.setattr(control_module, "atomic_write_json", serialized_replace)

    def concurrent_read(original):
        def read():
            value = original()
            # Without a shared write lock both writers read the same old
            # value. With the lock the first writer times out, commits, and
            # the second reads the updated value.
            try:
                readers.wait(timeout=0.2)
            except BrokenBarrierError:
                pass
            return value
        return read

    for control in (pause_control, cancel_control):
        monkeypatch.setattr(control, "_read", concurrent_read(control._read))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(pause_control.request_pause), pool.submit(cancel_control.request_cancel)]
        for job in jobs:
            job.result(timeout=5)

    stored = read_json(tmp_path / "control.json")
    assert stored == {"pause_requested": True, "cancel_requested": True}
