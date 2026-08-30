"""Contracts for IterationContext and the Agent-bound simulation MCP surface."""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from generative_agents.modules.memory import Event
from generative_agents.modules.game import Game
from generative_agents.modules.maze import Maze
from generative_agents.runtime.brain import BrainRuntime
from generative_agents.runtime.capabilities import SimulationMCPServer
from generative_agents.runtime.iteration import IterationContext
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.runtime.results import StepResultBuilder
from generative_agents.skills import SkillRunResult
from generative_agents.skills import MemoryStream, SkillLoopError


class _Tile:
    address = ["用户世界", "社区", "公园", "长椅"]
    collision = False
    spatial_semantics = (
        {
            "kind": "WORLD",
            "id": "world",
            "name": "用户世界",
            "semantic": "一个由用户定义的安静世界",
        },
    )

    def get_address(self, *args, **kwargs):
        return list(self.address)

    def get_events(self):
        return (Event("长椅", "位于", "公园", address=list(self.address)),)


class _Maze:
    maze_width = 4
    maze_height = 4

    def __init__(self):
        self.tile = _Tile()
        self.address_tiles = {"用户世界:社区:公园:长椅": {(2, 1)}}

    def tile_at(self, coord):
        return self.tile

    def get_address_tiles(self, address):
        return self.address_tiles[":".join(address)]

    def find_path(self, source, target):
        return [tuple(source), tuple(target)]

    def semantic_nodes_in_scope(self, _coord, _radius):
        return [
            {
                **dict(item),
                "address": ["用户世界"],
                "distance_tiles": 0.0,
                "relation": "CURRENT",
            }
            for item in self.tile.spatial_semantics
        ]

    def events_in_scope(self, _coord, _radius):
        return [
            {
                **event.to_dict(),
                "coord": (
                    list(self.tile.coord) if hasattr(self.tile, "coord") else [1, 1]
                ),
                "distance_tiles": 0.0,
            }
            for event in self.tile.get_events()
        ]


class _Objects:
    def nearby(self, coord):
        return []


class _Agent:
    name = "小林"
    coord = (1, 1)
    percept_config = {"vision_r": 2, "att_bandwidth": 8}

    def get_tile(self):
        return _Tile()

    def get_event(self):
        return Event(self.name, "观察", "公园", address=_Tile.address)


def _server():
    agent = _Agent()
    game = SimpleNamespace(
        maze=_Maze(),
        agents={"agent-1": agent},
        agent_keys_by_name={agent.name: "agent-1"},
        game_object_interactions=_Objects(),
        get_agent=lambda key: agent,
    )
    iteration = IterationContext(
        run_id=uuid4(),
        attempt_id=uuid4(),
        agent_key="agent-1",
        agent_name=agent.name,
        step_no=1,
        total_steps=3,
        now=datetime(2026, 8, 27, 11, 13, 51, tzinfo=timezone.utc),
        stride_minutes=10,
        coord=agent.coord,
        address=tuple(_Tile.address),
        spatial_semantics=_Tile.spatial_semantics,
    )
    return SimulationMCPServer(game, iteration)


def test_iteration_context_exposes_virtual_time_and_four_layer_semantics():
    server = _server()

    perceived = server.call("world-perceive", {"radius_tiles": 0})

    assert perceived["isError"] is False
    text = perceived["content"][0]["text"]
    assert "2026-08-27T11:13:51+00:00" in text
    assert "一个由用户定义的安静世界" in text
    assert "用户世界" in text


def test_world_perceive_uses_semantic_index_and_clamps_agent_attention_contract():
    width, height = 48, 32
    hierarchy_nodes = [
        {
            "id": "world-1",
            "kind": "WORLD",
            "parent_id": None,
            "name": "用户世界",
            "semantic": "用户定义的完整世界",
            "bounds": {"x": 0, "y": 0, "width": width, "height": height},
        },
        {
            "id": "sector-1",
            "kind": "SECTOR",
            "parent_id": "world-1",
            "name": "生活区",
            "semantic": "住宅和公共设施所在区域",
            "bounds": {"x": 0, "y": 0, "width": width, "height": height},
        },
        {
            "id": "arena-1",
            "kind": "ARENA",
            "parent_id": "sector-1",
            "name": "咖啡厅",
            "semantic": "供 Agent 喝咖啡和交谈的场所",
            "bounds": {"x": 8, "y": 4, "width": 32, "height": 24},
        },
        {
            "id": "object-1",
            "kind": "GAME_OBJECT",
            "parent_id": "arena-1",
            "name": "咖啡水吧",
            "semantic": "制作和领取咖啡的设施",
            "bounds": {"x": 23, "y": 15, "width": 3, "height": 2},
        },
    ]
    semantics = [
        {
            "id": item["id"],
            "kind": item["kind"],
            "name": item["name"],
            "semantic": item["semantic"],
        }
        for item in hierarchy_nodes
    ]
    tiles = []
    for y in range(height):
        for x in range(width):
            address = ["用户世界", "生活区"]
            tile_semantics = semantics[:2]
            if 8 <= x < 40 and 4 <= y < 28:
                address.append("咖啡厅")
                tile_semantics = semantics[:3]
            if 23 <= x < 26 and 15 <= y < 17:
                address.append("咖啡水吧")
                tile_semantics = semantics
            tiles.append(
                {
                    "coord": [x, y],
                    "address": address,
                    "collision": False,
                    "spatial_semantics": tile_semantics,
                }
            )
    maze = Maze(
        {
            "size": [height, width],
            "tile_size": 1,
            "world": "用户世界",
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": tiles,
            "editor_v2": {
                "root_node_id": "world-1",
                "hierarchy_nodes": hierarchy_nodes,
            },
        },
        logging.getLogger(__name__),
        random.Random(7),
    )
    duplicate = Event(
        "咖啡水吧",
        "状态为",
        "空闲",
        address=["用户世界", "生活区", "咖啡厅", "咖啡水吧"],
    )
    maze.tile_at((24, 15)).add_event(duplicate)
    maze.tile_at((25, 15)).add_event(duplicate)

    class _IndexedAgent(_Agent):
        coord = (24, 15)
        percept_config = {"vision_r": 8, "att_bandwidth": 8}

        def get_tile(self):
            return maze.tile_at(self.coord)

    indexed_agent = _IndexedAgent()
    game = SimpleNamespace(
        maze=maze,
        agents={"agent-1": indexed_agent},
        agent_keys_by_name={indexed_agent.name: "agent-1"},
        game_object_interactions=_Objects(),
        get_agent=lambda _key: indexed_agent,
    )
    tile = maze.tile_at(indexed_agent.coord)
    iteration = IterationContext(
        run_id=uuid4(),
        attempt_id=uuid4(),
        agent_key="agent-1",
        agent_name=indexed_agent.name,
        step_no=1,
        total_steps=3,
        now=datetime(2026, 8, 27, 11, 13, 51, tzinfo=timezone.utc),
        stride_minutes=10,
        coord=indexed_agent.coord,
        address=tuple(tile.address),
        spatial_semantics=tuple(tile.spatial_semantics),
    )

    response = SimulationMCPServer(game, iteration).call(
        "world-perceive", {"radius_tiles": 20}
    )
    payload = json.loads(response["content"][0]["text"])

    assert response["isError"] is False
    assert payload["requested_radius_tiles"] == 20
    assert payload["radius_tiles"] == 8
    assert "tiles" not in payload
    assert payload["current_location"]["spatial_node_ids"] == [
        "world-1",
        "sector-1",
        "arena-1",
        "object-1",
    ]
    assert [item["id"] for item in payload["spatial_nodes"]] == [
        "world-1",
        "sector-1",
        "arena-1",
    ]
    assert [item["object_key"] for item in payload["game_objects"]] == ["object-1"]
    assert (
        len(
            [
                item
                for item in payload["events"]
                if item["subject"] == "咖啡水吧" and item["predicate"] == "状态为"
            ]
        )
        == 1
    )
    assert len(response["content"][0]["text"]) < 8_000


def test_world_act_accepts_exactly_one_replayable_action_per_iteration():
    server = _server()

    moved = server.call(
        "world-act",
        {
            "action_type": "MOVE",
            "target_address": ["用户世界", "社区", "公园", "长椅"],
            "description": "小林走向长椅",
        },
    )
    rejected = server.call("world-act", {"action_type": "WAIT"})

    assert moved["isError"] is False
    assert server.action is not None
    assert server.action.action_type == "MOVE"
    assert server.action.path == ((2, 1),)
    assert rejected["isError"] is True
    assert "already selected" in rejected["content"][0]["text"]


def test_world_act_accepts_unencoded_activity_as_event_semantics():
    server = _server()

    acted = server.call(
        "world-act",
        {
            "action_type": "ACT",
            "predicate": "喝",
            "object": "咖啡",
            "description": "小林在水吧喝咖啡",
            "emoji": "☕",
        },
    )

    assert acted["isError"] is False
    assert server.action is not None
    assert server.action.action_type == "ACT"
    assert server.action.arguments["predicate"] == "喝"
    assert server.action.arguments["object"] == "咖啡"
    assert server.action.arguments["target_coord"] == [1, 1]
    assert server.action.arguments["target_address"] == _Tile.address


@pytest.mark.parametrize(
    "arguments,error_fragment",
    [
        (
            {
                "action_type": "ACT",
                "predicate": "喝",
                "object": "咖啡",
                "target_coord": [2, 1],
            },
            "current coordinate",
        ),
        (
            {
                "action_type": "ACT",
                "predicate": "喝",
                "object": "咖啡",
                "target_address": ["用户世界", "社区", "咖啡馆", "水吧"],
            },
            "current address",
        ),
        (
            {
                "action_type": "ACT",
                "predicate": "喝",
                "object": "咖啡",
                "object_key": "other-object",
            },
            "current Game Object",
        ),
    ],
)
def test_world_act_rejects_activity_that_claims_another_location(
    arguments, error_fragment
):
    server = _server()
    result = server.call("world-act", arguments)

    assert result["isError"] is True
    assert error_fragment in result["content"][0]["text"]
    assert server.action is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"action_type": "ACT", "predicate": "喝"},
        {"action_type": "ACT", "object": "咖啡"},
    ],
)
def test_world_act_rejects_activity_without_complete_event_semantics(arguments):
    result = _server().call("world-act", arguments)

    assert result["isError"] is True
    assert "predicate and object" in result["content"][0]["text"]


def test_replay_world_event_requires_spo_and_structured_payload():
    builder = StepResultBuilder(
        run_id=uuid4(),
        attempt_id=uuid4(),
        step_no=1,
        virtual_time=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    collector = StepResultCollector(builder, name_to_key={})

    collector.capture_event(
        {
            "kind": "world_domain_event",
            "event_type": "GAME_OBJECT_STATE_CHANGED",
            "agent_keys": ("agent-1",),
            "subject": "bench-1",
            "predicate": "状态变为",
            "object": "occupied",
            "structured_payload": {
                "object_key": "bench-1",
                "before": {"occupied": False},
                "after": {"occupied": True},
            },
        }
    )
    result = collector.freeze()

    assert result.domain_events[0].payload["subject"] == "bench-1"
    assert result.domain_events[0].payload["title"] == "bench-1状态变为occupied"
    assert result.domain_events[0].payload["detail"] == "bench-1 / 状态变为 / occupied"
    assert result.domain_events[0].payload["structured_payload"]["after"] == {
        "occupied": True
    }
    with pytest.raises(ValueError, match="structured_payload"):
        collector.capture_event(
            {
                "kind": "world_domain_event",
                "event_type": "BROKEN",
                "agent_keys": (),
                "subject": "bench-1",
                "predicate": "状态变为",
                "object": "occupied",
            }
        )


def test_game_object_skill_response_is_delivered_once_in_next_iteration_context(
    monkeypatch,
):
    captured_contexts = []

    class _CapturingSkillRuntime:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, skill, _task, *, context):
            captured_contexts.append(context)
            return SkillRunResult(skill=skill, output_text="accepted", trace=())

    monkeypatch.setattr(
        "generative_agents.runtime.brain.SkillRuntime", _CapturingSkillRuntime
    )
    agent = _Agent()
    agent.scratch = SimpleNamespace(currently="刚刚查看红绿灯")
    agent.associate = SimpleNamespace(abstract=lambda: {})
    agent.schedule = SimpleNamespace(abstract=lambda: {})
    agent.spatial = SimpleNamespace(tree={}, address={})
    agent.concepts = []
    agent.chats = []
    game = Game.__new__(Game)
    game.agents = {"agent-1": agent}
    game._external_observation_inbox = {"agent-1": []}
    game.context = SimpleNamespace(
        run_id=uuid4(),
        attempt_id=uuid4(),
        clock=SimpleNamespace(
            get_date=lambda: datetime(2026, 8, 27, 11, 20, tzinfo=timezone.utc)
        ),
    )
    response = "当前为行人绿灯，车辆已经停止，可以安全通过。"
    game.queue_external_observation(
        "agent-1",
        {
            "object_key": "signal-1",
            "object_name": "行人信号灯",
            "interaction_key": "query-signal",
            "skill_name": "traffic-signal-state",
            "observed_step": 1,
            "observed_at": "2026-08-27T11:10:00+00:00",
            "request": "现在可以过马路吗？",
            "response": response,
            "trace": [{"event": "internal-audit-only"}],
        },
    )
    brain = BrainRuntime(
        SimpleNamespace(
            normalize_name=lambda name: name,
            get=lambda _name: SimpleNamespace(kind="brain", revision="revision-1"),
        ),
        brain_skill="stanford-town-brain",
        model_config={"model": "test-model"},
    )

    first = brain.run_step(
        game,
        "agent-1",
        step_no=2,
        total_steps=3,
        stride_minutes=10,
    )
    second = brain.run_step(
        game,
        "agent-1",
        step_no=3,
        total_steps=3,
        stride_minutes=10,
    )

    delivered = captured_contexts[0]["IterationContext"]["variables"][
        "external_observations"
    ]
    assert delivered[0]["response"] == response
    assert delivered[0]["kind"] == "GAME_OBJECT_SKILL_RESPONSE"
    assert "trace" not in delivered[0]
    assert first["info"]["external_observations"] == delivered
    assert (
        captured_contexts[1]["IterationContext"]["variables"]["external_observations"]
        == []
    )
    assert second["info"]["external_observations"] == []


def test_game_keeps_conversation_thread_and_message_sequence_across_steps():
    game = Game.__new__(Game)
    game.context = SimpleNamespace(run_id=uuid4())
    game._conversation_threads = {}
    game._open_conversation_by_participants = {}
    game._conversation_sequence = 0

    first = game.record_conversation_message("lin", ("zhou",))
    second = game.record_conversation_message("zhou", ("lin",))
    ended = game.record_conversation_message("lin", ("zhou",), end_conversation=True)
    restarted = game.record_conversation_message("zhou", ("lin",))

    assert (
        first["conversation_id"]
        == second["conversation_id"]
        == ended["conversation_id"]
    )
    assert [
        first["message_sequence"],
        second["message_sequence"],
        ended["message_sequence"],
    ] == [1, 2, 3]
    assert ended["ended_reason"] == "EXPLICIT_END"
    assert restarted["conversation_id"] != ended["conversation_id"]


def test_brain_quality_report_flags_repeated_read_without_changing_execution_state():
    brain = BrainRuntime.__new__(BrainRuntime)
    brain.brain_skill = "test-brain"
    brain.registry = SimpleNamespace(
        get=lambda _name: SimpleNamespace(markdown="第 2 步等待，第 3 步再检索")
    )
    brain.model_client = None
    brain.logger = None
    brain._audit_records = [
        {
            "agent_key": "lin",
            "step_no": step,
            "runtime_signals": [],
            "mcp_calls": [
                {
                    "tool": "memory-stream-search",
                    "input": '{"query":"昨天的约定"}',
                    "output": '[{"content":"约定"}]',
                }
            ],
        }
        for step in (22, 23)
    ]

    report = brain.evaluate_quality()

    assert report["quality_status"] == "WARNING"
    assert report["execution_status_affected"] is False
    assert any(
        issue["code"] == "REPEATED_READ_WITHOUT_PROGRESS" for issue in report["issues"]
    )


def test_recoverable_brain_failure_rolls_back_partial_action_and_memory(
    tmp_path, monkeypatch
):
    run_id, attempt_id = uuid4(), uuid4()
    now = datetime(2026, 8, 27, 11, 20, tzinfo=timezone.utc)
    memory = MemoryStream(
        tmp_path / "brain-memory.sqlite",
        run_id=run_id,
        attempt_id=attempt_id,
    )
    memory.begin_step(1, now)

    class _PartiallyFailingSkillRuntime:
        def __init__(self, *_args, mcp, **_kwargs):
            self.mcp = mcp

        def run(self, _skill, _task, *, context):
            self.mcp.call(
                "memory-stream-append",
                {"content": "尚未提交却被提前写成完成事实"},
            )
            self.mcp.call(
                "world-act",
                {
                    "action_type": "ACT",
                    "predicate": "完成",
                    "object": "错误动作",
                },
            )
            raise SkillLoopError(
                "no semantic progress",
                trace=(
                    {
                        "event": "mcp.call",
                        "skill": "test-brain",
                        "tool": "memory-stream-append",
                        "input_text": '{}',
                        "output_text": '{}',
                        "is_error": False,
                    },
                    {
                        "event": "loop.detected",
                        "skill": "test-brain",
                        "tool": "call_skill",
                    },
                ),
            )

    monkeypatch.setattr(
        "generative_agents.runtime.brain.SkillRuntime",
        _PartiallyFailingSkillRuntime,
    )
    agent = _Agent()
    agent.scratch = SimpleNamespace(currently="准备行动")
    agent.associate = SimpleNamespace(abstract=lambda: {})
    agent.schedule = SimpleNamespace(abstract=lambda: {})
    agent.spatial = SimpleNamespace(tree={}, address={})
    agent.concepts = []
    agent.chats = []
    game = SimpleNamespace(
        context=SimpleNamespace(
            run_id=run_id,
            attempt_id=attempt_id,
            clock=SimpleNamespace(get_date=lambda: now),
        ),
        get_agent=lambda _key: agent,
        agents={"agent-1": agent},
        agent_keys_by_name={agent.name: "agent-1"},
        maze=_Maze(),
        game_object_interactions=_Objects(),
    )
    registry = SimpleNamespace(
        normalize_name=lambda name: name,
        get=lambda _name: SimpleNamespace(kind="brain", revision="revision-1"),
    )
    brain = BrainRuntime(
        registry,
        brain_skill="test-brain",
        model_config={"model": "test-model"},
        memory_stream=memory,
    )

    outcome = brain.run_step(
        game,
        "agent-1",
        step_no=1,
        total_steps=3,
        stride_minutes=10,
    )

    assert outcome["world_action"]["action_type"] == "WAIT"
    assert memory.search(agent_key="agent-1") == []
    assert memory.drain_result_events() == ()
    trace = outcome["events"][0]["trace"]
    assert [item["event"] for item in trace] == [
        "mcp.call",
        "loop.detected",
        "brain.rollback",
        "brain.fallback",
    ]


def test_failed_execution_quality_is_partial_and_does_not_call_model():
    brain = BrainRuntime.__new__(BrainRuntime)
    brain.brain_skill = "test-brain"
    brain.model_client = SimpleNamespace(
        chat_completion=lambda *_args, **_kwargs: pytest.fail(
            "failed execution quality must be deterministic"
        )
    )
    brain.logger = None
    brain._audit_records = []

    report = brain.evaluate_quality(
        include_model=False,
        execution_error={"code": "SKILL_LOOP", "message": "budget exhausted"},
    )

    assert report["quality_status"] == "WARNING"
    assert report["evaluator"]["status"] == "SKIPPED"
    assert report["issues"] == [
        {
            "code": "RUN_EXECUTION_FAILED",
            "severity": "ERROR",
            "agent_key": None,
            "step_no": None,
            "message": "运行在完成全部 Step 前失败；报告仅覆盖已执行部分。",
            "evidence": {"code": "SKILL_LOOP", "message": "budget exhausted"},
        }
    ]
