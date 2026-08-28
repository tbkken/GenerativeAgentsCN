"""基础能力回归测试：覆盖 ``test_simulation_continuity`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from generative_agents.modules.agent import Agent, valid_chat_message
from generative_agents.modules.memory import Event, Schedule
from generative_agents.runtime.context import RunControl, SimulationClock
from generative_agents.runtime.results import ActivityKind
from generative_agents.start import SimulationRunner


class _Tile:
    """为 ``_Tile`` 相关场景组织共享测试状态、输入或断言。"""
    def get_address(self):
        """为本测试模块封装 ``get_address`` 辅助步骤，减少重复的场景搭建代码。"""
        return ["world", "campus", "road", "tile"]


class _MovingAgent:
    """为 ``_MovingAgent`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.coord = (0, 0)
        self.path = []
        self._event = Event(
            "Klaus",
            "is",
            "walking to the library",
            address=["world", "campus", "library", "desk"],
        )

    def move(self, coord, path=None):
        """为本测试模块封装 ``move`` 辅助步骤，减少重复的场景搭建代码。"""
        self.coord = tuple(coord)
        self.path = list(path or ())
        return {}

    def get_event(self, as_act=True):
        """为本测试模块封装 ``get_event`` 辅助步骤，减少重复的场景搭建代码。"""
        return self._event if as_act else None

    def get_tile(self):
        """为本测试模块封装 ``get_tile`` 辅助步骤，减少重复的场景搭建代码。"""
        return _Tile()


class _MovementGame:
    """为 ``_MovementGame`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.agent = _MovingAgent()
        self.agents = {"resident-005": self.agent}
        self.agent_keys_by_name = {"Klaus": "resident-005"}
        self.initial_route = [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0)]

    def reset_game(self):
        """为本测试模块封装 ``reset_game`` 辅助步骤，减少重复的场景搭建代码。"""
        pass

    def get_agent(self, _agent_key):
        """为本测试模块封装 ``get_agent`` 辅助步骤，减少重复的场景搭建代码。"""
        return self.agent

    def agent_think(
        self,
        _agent_key,
        status,
        *,
        step_no,
        total_steps,
        stride_minutes,
    ):
        """为本测试模块封装 ``agent_think`` 辅助步骤，减少重复的场景搭建代码。"""
        self.agent.move(status["coord"], status.get("path"))
        route = list(self.agent.path or self.initial_route)
        return {
            "plan": {"path": route},
            "info": {
                "currently": "travelling",
                "associate": {},
                "concepts": {},
                "action": {},
                "schedule": {},
            },
            "events": (),
        }

    def commit_world_action(
        self,
        _agent_key,
        outcome,
        *,
        stride_minutes,
        movement_budget,
    ):
        planned_path = tuple(tuple(coord) for coord in outcome["plan"]["path"])
        from_coord = tuple(self.agent.coord)
        consumed = planned_path[:movement_budget]
        remaining = planned_path[len(consumed) :]
        self.agent.move(consumed[-1] if consumed else from_coord, remaining)
        return {
            "outcome": outcome,
            "planned_path": planned_path,
            "executed_path": (from_coord, *consumed),
            "remaining_path": remaining,
        }


class _Committer:
    """为 ``_Committer`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.results = []

    def commit(self, result, *, force_checkpoint):
        """为本测试模块封装 ``commit`` 辅助步骤，减少重复的场景搭建代码。"""
        self.results.append(result)


def test_runner_consumes_route_by_budget_and_keeps_the_remainder_for_resume():
    """回归验证 ``test_runner_consumes_route_by_budget_and_keeps_the_remainder_for_resume`` 所描述的业务结果、故障边界和隔离约束。"""
    clock = SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    context = SimpleNamespace(
        run_id=uuid4(),
        attempt_id=uuid4(),
        clock=clock,
        control=RunControl(),
        algorithm=SimpleNamespace(movement_tiles_per_minute=2),
    )
    game = _MovementGame()
    committer = _Committer()
    runner = SimulationRunner(context, game, committer)

    runner.run(2, stride_minutes=1)

    first, second = committer.results
    assert first.agents[0].from_coord == (0, 0)
    assert first.agents[0].to_coord == (2, 0)
    assert first.agents[0].path == ((0, 0), (1, 0), (2, 0))
    assert first.agents[0].activity_kind is ActivityKind.MOVING
    assert first.agents[0].decision_context["planned_path"][-1] == [5, 0]
    assert first.agents[0].decision_context["remaining_path"] == [
        [3, 0],
        [4, 0],
        [5, 0],
    ]
    assert second.agents[0].from_coord == (2, 0)
    assert second.agents[0].to_coord == (4, 0)
    assert second.agents[0].path == ((2, 0), (3, 0), (4, 0))
    assert game.agent.path == [(5, 0)]
    assert runner.agent_status["resident-005"]["path"] == ((5, 0),)


def test_chat_cooldown_uses_durable_pair_timestamp_not_semantic_retrieval():
    """回归验证 ``test_chat_cooldown_uses_durable_pair_timestamp_not_semantic_retrieval`` 所描述的业务结果、故障边界和隔离约束。"""
    clock = SimulationClock(datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc))

    def build(name, agent_key):
        """构造当前测试场景所需的 ``build`` 数据、文件或受控对象。"""
        agent = Agent.__new__(Agent)
        agent.name = name
        agent.agent_key = agent_key
        agent.schedule = SimpleNamespace(daily_schedule=[{"describe": "research"}])
        agent.path = []
        agent.chat_cooldown_minutes = 60
        agent.chat_stop_after_hour = 23
        agent._clock = clock
        agent.last_chat_at = {}
        agent.logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)
        agent._skip_react = lambda _other: False
        agent.get_event = lambda: Event(
            name,
            "is",
            "researching",
            address=["world", "campus", "library", "desk"],
        )
        return agent

    klaus = build("Klaus", "resident-005")
    aisha = build("Aisha", "resident-024")
    klaus.last_chat_at[aisha.agent_key] = clock.get_date() - timedelta(minutes=20)
    klaus.associate = SimpleNamespace(
        retrieve_chats=lambda _name: (_ for _ in ()).throw(
            AssertionError("cooldown must not depend on vector retrieval")
        )
    )

    assert klaus._chat_with(aisha, {}) is False


def test_schedule_chat_is_a_local_splice_that_preserves_unaffected_work():
    """回归验证 ``test_schedule_chat_is_a_local_splice_that_preserves_unaffected_work`` 所描述的业务结果、故障边界和隔离约束。"""
    clock = SimulationClock(datetime(2026, 1, 1, 13, 25, tzinfo=timezone.utc))
    schedule = Schedule(
        clock=clock,
        daily_schedule=[
            {
                "idx": 0,
                "describe": "research",
                "start": 780,
                "duration": 60,
                "decompose": [
                    {"idx": 0, "describe": "read", "start": 780, "duration": 30},
                    {"idx": 1, "describe": "write", "start": 810, "duration": 30},
                ],
            }
        ],
    )

    assert schedule.insert_interruption("talk with Aisha", clock.get_date(), 2)
    items = schedule.daily_schedule[0]["decompose"]

    assert [(item["start"], item["duration"], item["describe"]) for item in items] == [
        (780, 25, "read"),
        (805, 2, "talk with Aisha"),
        (807, 3, "read"),
        (810, 30, "write"),
    ]


def test_restored_schedule_compares_the_simulation_local_calendar_day():
    """回归验证 ``test_restored_schedule_compares_the_simulation_local_calendar_day`` 所描述的业务结果、故障边界和隔离约束。"""
    clock = SimulationClock(
        datetime(2026, 2, 13, 0, 30, tzinfo=timezone(timedelta(hours=8)))
    )
    schedule = Schedule(
        clock=clock,
        create="20260213-00:00:00",
        daily_schedule=[
            {
                "idx": 0,
                "describe": "sleep",
                "start": 0,
                "duration": 60,
                "decompose": {},
            }
        ],
    )

    assert schedule.scheduled()


def test_chat_quality_guard_rejects_placeholders_and_exact_repeats():
    """回归验证 ``test_chat_quality_guard_rejects_placeholders_and_exact_repeats`` 所描述的业务结果、故障边界和隔离约束。"""
    assert valid_chat_message("我们一起核对住房政策数据。")
    assert not valid_chat_message("填坑")
    assert not valid_chat_message("TODO")
    assert not valid_chat_message(
        "我们一起核对住房政策数据。",
        [("Aisha", "我们一起核对住房政策数据。")],
    )
