"""Real file package execution, checkpoint resume and replay for autonomous objects."""

import json
from generative_agents.ga_protocol.schemas.manifests import RunState
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_runtime.lifecycle.executor import RuntimeModelRegistry
from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder
from generative_agents.ga_studio.experiments.builder import SkillSource
from tests.runtime.test_object_skill_runtime import ToolModel, world
from tests.test_portable_package_protocol import _definition


def test_package_object_skill_runs_without_agents_and_resumes_from_committed_fact(tmp_path, monkeypatch):
    definition = _definition()
    definition["world"]["definition"] = world()
    definition["simulation"].update(stride_minutes=1, max_steps=3)
    definition["engine"]["brain_skill"] = "test-brain"
    sources = []
    for name, kind in [("test-brain", "brain"), ("facility", "object")]:
        source = tmp_path / "author" / name / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text(f"---\nname: {name}\ndescription: 自主维护设施状态并回应请求\n---\n\n根据当前时间维护灯色。\n", encoding="utf-8")
        sources.append(SkillSource(name, kind, source))
    package = ExperimentPackageBuilder().build_directory(
        tmp_path / "experiment", definition=definition, skills=sources,
        brain_skill="test-brain", object_roots=["facility"],
    )
    validate_experiment_directory(package)
    exchange = seal_directory(package, tmp_path / "object-experiment.gaexp")
    run_root = RunService().create(exchange, tmp_path / "run", requested_steps=3)

    def choose(messages, kwargs):
        step = kwargs["step_no"]
        if step == 1:
            RunService().request_pause(run_root)
        text = messages[1]["content"]
        context = json.loads(text[text.index('{'):])["IterationContext"]
        state = context["variables"]["object_state"]
        if step == 2:
            assert state["state"] == "GREEN"
            assert context["variables"]["last_action"]["step_no"] == 1
            assert "switch:1" in context["variables"]["recent_action_keys"]
        return "world-act", {"action_type": "SET_OBJECT_STATE",
                             "state_patch": {"state": "RED" if step == 2 else "GREEN"},
                             "idempotency_key": f"switch:{step}"}

    model = ToolModel(choose)
    monkeypatch.setattr(RuntimeModelRegistry, "get", lambda *_: model)
    first = execute_run_directory(run_root)
    assert first.status == RunState.PAUSED and first.committed_step == 1
    first_attempt = first.active_attempt_id
    # Changing author content cannot affect the run's embedded object Skill.
    sources[1].skill_file.write_text("unrelated author edit", encoding="utf-8")
    final = RunService().resume(run_root)
    assert final.status == RunState.COMPLETED and final.committed_step == 3
    assert final.run_id == first.run_id and final.active_attempt_id != first_attempt
    assert [kwargs["step_no"] for _, kwargs in model.calls] == [1, 2, 3]
    with ReplayReader(run_root) as replay:
        assert replay.state_at(1)["object_states"]["camera"]["state"] == "GREEN"
        assert replay.state_at(2)["object_states"]["camera"]["state"] == "RED"
        assert replay.state_at(3)["object_states"]["camera"]["state"] == "GREEN"
        frames = list(replay.iter_steps())
    assert all(not frame["agents"] for frame in frames)
    assert sum(event["event_type"] == "GAME_OBJECT_STATE_CHANGED" for frame in frames for event in frame["domain_events"]) == 3
    report = read_json(run_root / "artifacts" / "quality-report.json")
    assert report["evaluated_object_steps"] == 3
    assert report["evaluated_agent_steps"] == 0
    archive = RunService().seal(run_root, tmp_path / "object-run.garun")
    with ReplayReader(archive) as replay:
        assert replay.state_at(2)["object_states"]["camera"]["state"] == "RED"
        assert len(list(replay.iter_steps())) == 3
