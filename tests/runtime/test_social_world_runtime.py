"""1D communications preserve visibility, ordering and the atomic Step boundary."""
from __future__ import annotations

import copy
import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_protocol.schemas.facts import StepResult
from generative_agents.ga_runtime.capabilities.server import SimulationAPI
from generative_agents.ga_runtime.engine.collector import StepResultCollector
from generative_agents.ga_runtime.engine.context import SimulationClock
from generative_agents.ga_runtime.engine.iteration import IterationContext
from generative_agents.ga_runtime.engine.results import StepResultBuilder
from generative_agents.ga_runtime.engine.scheduler import SimulationRunner
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.memory.stream import FileMemoryStream
from generative_agents.ga_runtime.models.retry import ModelCallCancelled
from generative_agents.ga_runtime.storage.preview import read_runtime_preview


def make_game(tmp_path, presentation="chat", *, run_id=None):
    run_id = run_id or uuid4()
    context = SimpleNamespace(run_id=run_id, attempt_id=uuid4(), logger=logging.getLogger(__name__),
        clock=SimulationClock(datetime(2026, 10, 22, 9, tzinfo=timezone.utc)), random=random.Random(7),
        paths=SimpleNamespace(root=tmp_path), control=SimpleNamespace(cancel_requested=False, pause_requested=False))
    context.memory_stream = FileMemoryStream(tmp_path / "memory", run_id=run_id, attempt_id=context.attempt_id)
    context.memory_stream.begin_step(1, context.clock.get_date())
    game = Game({"maze": {"environment_type": "social_1d", "presentation": presentation, "description": "讨论空间"},
                 "space_id": "discussion", "world_name": "讨论", "agents": {
                     key: {"name": key.upper(), "space_id": "discussion", "scratch": {},
                           "percept": {"att_bandwidth": 8}, "currently": ""} for key in ("a", "b", "c")}},
                {}, context=context)
    atomic_write_json(tmp_path / "run.json", {"run_id": str(run_id)})
    atomic_write_json(tmp_path / "status.json", {"run_id": str(run_id), "status": "RUNNING",
        "active_attempt_id": str(context.attempt_id), "committed_step": 0})
    return game


def api(game, key, step=1):
    context = game.context
    return SimulationAPI(game, IterationContext(run_id=context.run_id, attempt_id=context.attempt_id,
        agent_key=key, agent_name=key.upper(), step_no=step, total_steps=20, now=context.clock.get_date(),
        stride_minutes=1, coord=None, address=(), environment_type="social_1d", space_id=game.space_id,
        presentation=game.presentation), memory_stream=context.memory_stream)


def choose(game, key, step, **arguments):
    interface = api(game, key, step)
    response = interface.invoke("world-act", arguments)
    assert response["ok"], response
    return {"world_action": interface.action.as_dict(), "info": {"iteration_context": interface.iteration.as_dict()}, "events": ()}


def speak(game, key, step=1, **arguments):
    outcome = choose(game, key, step, action_type="SPEAK", **arguments)
    return game.commit_world_action(key, outcome, stride_minutes=1, movement_budget=4)["outcome"]


def test_same_step_messages_and_memory_have_real_sources_without_spatial_state(tmp_path):
    game = make_game(tmp_path)
    before = read_json(game.context.memory_stream.path)
    first = speak(game, "a", message="我建议先讨论成本")
    observed = api(game, "b").invoke("world-perceive", {})["result"]["messages"][0]
    assert observed["content"] == "我建议先讨论成本"
    assert read_json(game.context.memory_stream.path) == before  # Perception is read-only.
    saved = api(game, "b").invoke("memory-stream-append", {
        "content": "A建议先讨论成本", "source_event_id": observed["source_event_id"]})
    assert saved["ok"] and saved["result"]["source_message_id"] == observed["message_id"]
    retrieved = api(game, "b", 2).invoke("memory-stream-search", {"query": "成本"})["result"][0]
    assert retrieved["source_conversation_id"] == observed["conversation_id"]
    second = speak(game, "b", message="赞成", quoted_message_id=retrieved["source_message_id"])
    builder = StepResultBuilder(game.context.run_id, game.context.attempt_id, 1, game.context.clock.get_date())
    collector = StepResultCollector(builder, name_to_key=game.agent_keys_by_name)
    collector.capture_agent("a", game.agents["a"], None, first)
    collector.capture_agent("b", game.agents["b"], None, second)
    for event in game.context.memory_stream.drain_result_events():
        collector.capture_event(event)
    result = collector.freeze()
    assert str(result.domain_events[0].event_id) == observed["source_event_id"]
    assert result.memory_deltas[0].source_event_id == result.domain_events[0].event_id
    assert all(item.from_coord is None and item.to_coord is None and item.location == () for item in result.agents)
    assert StepResult.from_dict(result.to_dict()) == result


def test_private_content_and_references_never_escape_participants(tmp_path):
    game = make_game(tmp_path)
    speak(game, "a", message="秘密内容", visibility="private", participant_agent_keys=["b"])
    private = api(game, "b").invoke("world-perceive", {})["result"]["messages"][0]
    outsider = api(game, "c")
    assert outsider.invoke("world-perceive", {})["result"]["messages"] == []
    for arguments in ({"message_id": private["message_id"]}, {"conversation_id": private["conversation_id"]},
                      {"after_message_id": private["message_id"]}):
        assert not outsider.invoke("world-perceive", arguments)["ok"]
    assert not outsider.invoke("memory-stream-append", {"content": "猜测", "source_event_id": private["source_event_id"]})["ok"]
    assert not api(game, "b").invoke("world-act", {"action_type": "SPEAK", "message": "泄露", "quoted_message_id": private["message_id"]})["ok"]
    speak(game, "b", message="收到", visibility="private", participant_agent_keys=["a"],
          conversation_id=private["conversation_id"], reply_to_message_id=private["message_id"])
    assert len(api(game, "a").invoke("world-perceive", {})["result"]["messages"]) == 2
    assert api(game, "c").invoke("world-perceive", {})["result"]["messages"] == []
    assert "秘密" not in str(game.agents["a"].get_event().to_dict())


def test_forum_nested_replies_and_cross_topic_quotes(tmp_path):
    game = make_game(tmp_path, "forum")
    speak(game, "a", title="交通", message="怎样改善通勤")
    root = game.social.messages[-1]
    speak(game, "b", message="公交优先", reply_to_message_id=root["message_id"])
    parent = game.social.messages[-1]
    speak(game, "c", message="同意", reply_to_message_id=parent["message_id"])
    child = game.social.messages[-1]
    assert child["conversation_id"] == parent["conversation_id"] == root["conversation_id"]
    assert child["reply_to_message_id"] == parent["message_id"]
    speak(game, "a", 2, title="预算", message="投资要有计划", quoted_message_id=child["message_id"])
    other = game.social.messages[-1]
    assert other["conversation_id"] != root["conversation_id"]
    rejected = api(game, "b", 2).invoke("world-act", {"action_type": "SPEAK", "message": "冲突",
        "conversation_id": other["conversation_id"], "reply_to_message_id": root["message_id"]})
    assert not rejected["ok"]
    assert not api(game, "b", 2).invoke("world-act", {"action_type": "SPEAK", "message": "没标题"})["ok"]


def test_attention_pagination_keeps_public_history_accessible(tmp_path):
    game = make_game(tmp_path)
    for index, key in enumerate(("a", "b", "c"), 1):
        speak(game, key, message=f"意见{index}")
    game.agents["c"].percept_config["att_bandwidth"] = 1
    latest = api(game, "c").invoke("world-perceive", {"limit": 100})["result"]
    assert len(latest["messages"]) == 1 and latest["attention"]["truncated"]
    assert len(latest["participants"]) == 1 and latest["observer"]["agent_key"] == "c"
    roster = api(game, "c").invoke("world-perceive", {"participant_offset": latest["next_participant_offset"]})["result"]
    assert len(roster["participants"]) == 1 and roster["participants"] != latest["participants"]
    earlier = api(game, "c").invoke("world-perceive", {"before_message_id": latest["first_message_id"]})["result"]
    assert earlier["messages"][0]["content"] == "意见2"
    game.agents["c"].percept_config["att_bandwidth"] = 0
    assert api(game, "c").invoke("world-perceive", {})["result"]["messages"] == []


def test_wait_consumes_the_world_commit_allowance_and_survives_restore(tmp_path):
    game = make_game(tmp_path)
    wait = choose(game, "a", 1, action_type="WAIT", wait_reason="继续观察")
    game.commit_world_action("a", wait, stride_minutes=1, movement_budget=1)
    with pytest.raises(ValueError, match="already committed"):
        speak(game, "a", 1, message="重复动作")
    restored = make_game(tmp_path / "restored", run_id=game.context.run_id)
    restored.restore_runtime_state(game.snapshot_state())
    with pytest.raises(ValueError, match="already committed"):
        restored.commit_world_action("a", wait, stride_minutes=1, movement_budget=1)
    speak(restored, "a", 2, message="下一轮正常发言")


@pytest.mark.parametrize("failure", ["cancel", "failure"])
def test_preview_visible_mid_step_then_removed_with_message_and_memory_rollback(tmp_path, failure):
    game = make_game(tmp_path)
    results = []
    def think(key, _status, **kwargs):
        if key == "a":
            game.context.memory_stream.append(agent_key="a", content="尚未提交的观察")
            return choose(game, key, kwargs["step_no"], action_type="SPEAK", message="尚未提交")
        preview = read_runtime_preview(tmp_path)
        assert preview["uncommitted"] and preview["actor_sequence"] == 1
        assert preview["conversations"][0]["messages"][0]["content"] == "尚未提交"
        assert api(game, key).invoke("world-perceive", {})["result"]["messages"]
        if failure == "cancel":
            raise ModelCallCancelled("cancel requested")
        raise RuntimeError("injected failure")
    game.agent_think = think
    run = SimulationRunner(game.context, game, SimpleNamespace(commit=lambda result, **_: results.append(result)))
    with pytest.raises(RuntimeError):
        run.run(1, stride_minutes=1)
    assert results == [] and run.completed_steps == 0
    assert not (tmp_path / "runtime-preview.json").exists()
    assert game.social.messages == [] and read_json(game.context.memory_stream.path)["items"] == []


def test_silence_is_wait_and_preview_expires_after_commit(tmp_path):
    game = make_game(tmp_path)
    results = []
    game.agent_think = lambda key, _status, **kw: choose(game, key, kw["step_no"], action_type="WAIT", wait_reason="暂不补充，继续观察")
    run = SimulationRunner(game.context, game, SimpleNamespace(commit=lambda result, **_: results.append(result)))
    assert run.run(1, stride_minutes=1) == 1
    assert not results[0].conversations and len(results[0].agents) == 3
    assert all(item.event_type == "AGENT_WAITED" for item in results[0].domain_events)
    assert not (tmp_path / "runtime-preview.json").exists()
    assert not api(game, "a", 2).invoke("world-act", {"action_type": "WAIT"})["ok"]
    assert not api(game, "a", 2).invoke("world-act", {"action_type": "MOVE", "target_coord": [0, 0]})["ok"]


def test_checkpoint_restores_message_identity_and_reply_association(tmp_path):
    game = make_game(tmp_path / "first", "forum")
    speak(game, "a", title="主题", message="观点")
    original = copy.deepcopy(game.social.messages[0])
    snapshot = game.snapshot_state()
    restored = make_game(tmp_path / "next", "forum", run_id=game.context.run_id)
    restored.restore_runtime_state(snapshot)
    assert api(restored, "b", 2).invoke("world-perceive", {})["result"]["messages"][0] == original
    speak(restored, "b", 2, message="续跑回复", reply_to_message_id=original["message_id"])
    assert len(restored.social.messages) == 2
    assert restored.social.messages[1]["conversation_id"] == original["conversation_id"]


def test_preview_ignores_old_attempt_or_committed_boundary(tmp_path):
    game = make_game(tmp_path)
    preview = {"run_id": str(game.context.run_id), "attempt_id": str(uuid4()), "step_no": 1,
               "committed_step": 0, "uncommitted": True, "conversations": []}
    atomic_write_json(tmp_path / "runtime-preview.json", preview)
    assert read_runtime_preview(tmp_path) is None

    preview["attempt_id"] = str(game.context.attempt_id)
    atomic_write_json(tmp_path / "runtime-preview.json", preview)
    assert read_runtime_preview(tmp_path) == preview
    status = read_json(tmp_path / "status.json")
    status["committed_step"] = 1
    atomic_write_json(tmp_path / "status.json", status)
    assert read_runtime_preview(tmp_path) is None


def test_spatial_interfaces_do_not_advertise_or_accept_social_arguments():
    from tests.runtime.test_world_commit_event_sync import _game
    game, agent = _game()
    interface = SimulationAPI(game, IterationContext(run_id=uuid4(), attempt_id=uuid4(),
        agent_key=agent.agent_key, agent_name=agent.name, step_no=1, total_steps=1,
        now=game.context.clock.get_date(), stride_minutes=1, coord=agent.coord, address=()))
    descriptors = {item["name"]: item for item in interface.descriptors()}
    social_fields = {"visibility", "title", "reply_to_message_id", "quoted_message_id"}
    assert not social_fields & descriptors["world-act"]["parameters"]["properties"].keys()
    for field in social_fields:
        assert not interface.invoke("world-act", {"action_type": "WAIT", field: "social-only"})["ok"]


@pytest.mark.parametrize("presentation", ["chat", "forum"])
def test_packaged_brain_run_pause_resume_seal_and_replay(tmp_path, monkeypatch, presentation):
    from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from generative_agents.ga_runtime.lifecycle.control import FileRunControl
    from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory, RuntimeModelRegistry
    from generative_agents.ga_replay.reader import ReplayReader
    from generative_agents.ga_protocol.packages.io import seal_directory
    from tests.test_portable_package_protocol import _definition
    from tests.runtime.test_object_skill_runtime import ToolModel

    definition = _definition()
    definition["world"] = {"world_key": "discussion", "world_name": "交流空间", "assets": [], "definition": {
        "environment_type": "social_1d", "presentation": presentation, "description": "交流测试", "rules": "尊重事实"}}
    definition["agents"] = [{"agent_key": key, "name": key.upper(), "space_id": "discussion",
                             "scratch": {"age": 30}} for key in ("aa", "bb", "cc")]
    source = tmp_path / "author" / "SKILL.md"
    source.parent.mkdir()
    source.write_text("---\nname: test-brain\ndescription: 交流\n---\n# Brain\n"
                      "调用 @world-perceive，按需用 @memory-stream-append 保存观察。"
                      "用 @memory-stream-search 回忆。最后 @world-act 发言或等待。", encoding="utf-8")
    ExperimentPackageBuilder().build_directory(tmp_path / "experiment", definition=definition,
        skills=[SkillSource("test-brain", "brain", source)], brain_skill="test-brain")
    experiment_zip = seal_directory(tmp_path / "experiment", tmp_path / "experiment.gaexp")
    run_root = RunService().create(experiment_zip, tmp_path / "run", requested_steps=2)
    seen = []
    def choose_tool(messages, _kwargs):
        context = json.loads(messages[1]["content"].split("output schema):\n", 1)[1])["IterationContext"]
        key, step = context["agent"]["agent_key"], context["step"]["number"]
        assert "coord" not in context["agent"]
        assert context["environment"]["rules"] == "尊重事实"
        tool_results = [item for item in messages if item["role"] == "tool"]
        if not tool_results:
            seen.append((key, step))
            if step == 1 and key == "cc":
                FileRunControl(run_root).request_pause()
            return "world-perceive", {}
        if tool_results[-1]["name"] == "world-perceive":
            observed = json.loads(tool_results[-1]["content"])["messages"]
            if observed:
                return "memory-stream-append", {"content": observed[-1]["content"], "source_event_id": observed[-1]["source_event_id"]}
            return "world-act", {"action_type": "SPEAK", "message": "第一个观点", **({"title": "共同讨论"} if presentation == "forum" else {})}
        remembered = json.loads(tool_results[-1]["content"])
        if key == "cc":
            return ("world-act", {"action_type": "WAIT", "wait_reason": "继续观察其他观点"}) if step == 1 else (
                "world-act", {"action_type": "SPEAK", "visibility": "private", "participant_agent_keys": ["bb"],
                              "message": "这是只发给你的补充意见"})
        return "world-act", {"action_type": "SPEAK", "message": f"{key}的第{step}轮回复",
                             "reply_to_message_id": remembered["source_message_id"],
                             "quoted_message_id": remembered["source_message_id"]}
    model = ToolModel(choose_tool)
    monkeypatch.setattr(RuntimeModelRegistry, "get", lambda *_: model)
    first = execute_run_directory(run_root)
    assert first.status.value == "PAUSED" and first.committed_step == 1
    with ReplayReader(run_root) as reader:
        before = list(reader.iter_steps())
        assert len(before) == 1 and len(before[0]["conversations"]) == 2
        first_messages = [item for record in before[0]["conversations"] for item in record["messages"]]
    second = RunService().resume(run_root)
    assert second.status.value == "COMPLETED" and second.committed_step == 2
    assert second.run_id == first.run_id and second.active_attempt_id != first.active_attempt_id
    assert seen == [(key, step) for step in (1, 2) for key in ("aa", "bb", "cc")]
    atomic_write_json(run_root / "runtime-preview.json", {"uncommitted": True})
    sealed = RunService().seal(run_root, tmp_path / "complete.garun")
    import zipfile
    with zipfile.ZipFile(sealed) as archive:
        assert "runtime-preview.json" not in archive.namelist()
    with ReplayReader(sealed) as reader:
        frames = list(reader.iter_steps())
        assert [item for record in frames[0]["conversations"] for item in record["messages"]] == first_messages
        all_messages = [message for frame in frames for record in frame["conversations"] for message in record["messages"]]
        assert len(all_messages) == len({item["message_id"] for item in all_messages}) == 5
