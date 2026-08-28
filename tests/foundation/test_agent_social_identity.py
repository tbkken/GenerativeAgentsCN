"""基础能力回归测试：覆盖 ``test_agent_social_identity`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import random
from types import MappingProxyType, SimpleNamespace

from generative_agents.modules.agent import Agent
from generative_agents.modules.game import Game
from generative_agents.modules.memory.event import Event


def test_reaction_resolves_event_display_name_to_agent():
    """回归验证 ``test_reaction_resolves_event_display_name_to_agent`` 所描述的业务结果、故障边界和隔离约束。"""
    initiator = Agent.__new__(Agent)
    other = SimpleNamespace(name="阿伊莎", agent_key="resident-024")
    concept = SimpleNamespace(
        event=Event("阿伊莎", "此时", "撰写论文"),
        describe="阿伊莎正在撰写论文",
    )
    initiator.concepts = [concept]
    initiator._rng = random.Random(7)
    relation = {"events": [concept], "thoughts": []}
    initiator.associate = SimpleNamespace(get_relation=lambda _focus: relation)
    observed = []
    initiator._chat_with = lambda resolved, focus: observed.append(
        (resolved, focus)
    ) or True
    initiator._wait_other = lambda _resolved, _focus: False

    agents_by_name = MappingProxyType({"阿伊莎": other})
    assert initiator._reaction(agents_by_name) is True
    assert observed == [(other, relation)]


def test_game_keeps_runtime_keys_but_passes_name_index_to_cognition():
    """回归验证 ``test_game_keeps_runtime_keys_but_passes_name_index_to_cognition`` 所描述的业务结果、故障边界和隔离约束。"""
    observed_rosters = []

    class _Agent:
        """为 ``_Agent`` 相关场景组织共享测试状态、输入或断言。"""
        name = "克劳斯"
        last_record = 0
        concepts = []
        chats = []
        scratch = SimpleNamespace(currently="研究论文")
        associate = SimpleNamespace(abstract=lambda: {})
        action = SimpleNamespace(abstract=lambda: {})
        schedule = SimpleNamespace(abstract=lambda: {})

        def think(self, _status, roster):
            """为本测试模块封装 ``think`` 辅助步骤，减少重复的场景搭建代码。"""
            observed_rosters.append(roster)
            return {"name": self.name, "path": [], "emojis": {}}

        def get_tile(self):
            """为本测试模块封装 ``get_tile`` 辅助步骤，减少重复的场景搭建代码。"""
            return SimpleNamespace(get_address=lambda **_kwargs: "the Ville:图书馆")

        def llm_available(self):
            """为本测试模块封装 ``llm_available`` 辅助步骤，减少重复的场景搭建代码。"""
            return False

        def drain_result_events(self):
            """为本测试模块封装 ``drain_result_events`` 辅助步骤，减少重复的场景搭建代码。"""
            return ()

        def __str__(self):
            """为本测试模块封装 ``__str__`` 辅助步骤，减少重复的场景搭建代码。"""
            return self.name

    agent = _Agent()

    class _BrainRuntime:
        def run_step(
            self,
            runtime_game,
            agent_key,
            *,
            step_no,
            total_steps,
            stride_minutes,
        ):
            assert agent_key == "resident-005"
            assert (step_no, total_steps, stride_minutes) == (1, 1, 10)
            observed_rosters.append(runtime_game.agents_by_name)
            return {
                "world_action": {"action_type": "WAIT", "arguments": {}},
                "info": {},
                "events": (),
            }

    game = Game.__new__(Game)
    game.agents = {"resident-005": agent}
    game.agents_by_name = MappingProxyType({"克劳斯": agent})
    game.context = SimpleNamespace(
        brain_runtime=_BrainRuntime(),
        clock=SimpleNamespace(
            daily_duration=lambda: 0,
            get_date=lambda _fmt=None: "20260214-10:00:00",
        )
    )
    game.record_interval = 30
    game.logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)

    game.agent_think(
        "resident-005",
        {"coord": (0, 0), "path": ()},
        step_no=1,
        total_steps=1,
        stride_minutes=10,
    )

    assert game.get_agent("resident-005") is agent
    assert len(observed_rosters) == 1
    assert tuple(observed_rosters[0]) == ("克劳斯",)
    assert "resident-005" not in observed_rosters[0]


def test_find_path_excludes_tile_occupied_by_named_agent_event():
    """回归验证 ``test_find_path_excludes_tile_occupied_by_named_agent_event`` 所描述的业务结果、故障边界和隔离约束。"""
    occupied = (1, 0)
    available = (2, 0)

    class _Tile:
        """为 ``_Tile`` 相关场景组织共享测试状态、输入或断言。"""
        def __init__(self, events, address=None):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            self._events = events
            self._address = address or ["the Ville", "其他区域"]

        def get_events(self):
            """为本测试模块封装 ``get_events`` 辅助步骤，减少重复的场景搭建代码。"""
            return self._events

        def get_address(self):
            """为本测试模块封装 ``get_address`` 辅助步骤，减少重复的场景搭建代码。"""
            return self._address

    class _Maze:
        """为 ``_Maze`` 相关场景组织共享测试状态、输入或断言。"""
        def get_address_tiles(self, _address):
            """为本测试模块封装 ``get_address_tiles`` 辅助步骤，减少重复的场景搭建代码。"""
            return {occupied, available}

        def tile_at(self, coord):
            """为本测试模块封装 ``tile_at`` 辅助步骤，减少重复的场景搭建代码。"""
            if tuple(coord) == occupied:
                return _Tile([Event("阿伊莎", "此时", "阅读")])
            return _Tile([])

        def find_path(self, source, target):
            """为本测试模块封装 ``find_path`` 辅助步骤，减少重复的场景搭建代码。"""
            return [tuple(source), tuple(target)]

    agent = Agent.__new__(Agent)
    agent.path = []
    agent.coord = (0, 0)
    agent.maze = _Maze()
    agent._rng = random.Random(3)
    agent.get_event = lambda: Event(
        "克劳斯",
        "此时",
        "前往图书馆",
        address=["the Ville", "学院", "图书馆", "桌子"],
    )

    path = agent.find_path(
        MappingProxyType(
            {"阿伊莎": SimpleNamespace(name="阿伊莎", agent_key="resident-024")}
        )
    )
    assert path == [available]


def test_chat_result_uses_stable_agent_keys_after_name_resolution():
    """回归验证 ``test_chat_result_uses_stable_agent_keys_after_name_resolution`` 所描述的业务结果、故障边界和隔离约束。"""
    class _Schedule:
        """为 ``_Schedule`` 相关场景组织共享测试状态、输入或断言。"""
        daily_schedule = [{"describe": "研究论文"}]

    class _Associate:
        """为 ``_Associate`` 相关场景组织共享测试状态、输入或断言。"""
        def retrieve_chats(self, _name):
            """为本测试模块封装 ``retrieve_chats`` 辅助步骤，减少重复的场景搭建代码。"""
            return []

    class _Logger:
        """为 ``_Logger`` 相关场景组织共享测试状态、输入或断言。"""
        def info(self, *_args, **_kwargs):
            """为本测试模块封装 ``info`` 辅助步骤，减少重复的场景搭建代码。"""
            pass

    class _Clock:
        """为 ``_Clock`` 相关场景组织共享测试状态、输入或断言。"""
        def daily_duration(self, mode=None):
            """为本测试模块封装 ``daily_duration`` 辅助步骤，减少重复的场景搭建代码。"""
            return 10 if mode == "hour" else 600

        def get_date(self, fmt=None):
            """为本测试模块封装 ``get_date`` 辅助步骤，减少重复的场景搭建代码。"""
            if fmt:
                return "20260214-10:00"
            return SimpleNamespace()

    def build_agent(name, agent_key, response):
        """构造当前测试场景所需的 ``build_agent`` 数据、文件或受控对象。"""
        agent = Agent.__new__(Agent)
        agent.name = name
        agent.agent_key = agent_key
        agent.schedule = _Schedule()
        agent.associate = _Associate()
        agent.path = []
        agent.chat_cooldown_minutes = 60
        agent.chat_stop_after_hour = 23
        agent.chat_iter = 1
        agent.repeat_detection_enabled = True
        agent._clock = clock
        agent._algorithm = SimpleNamespace(chat_chars_per_minute=240)
        agent._result_events = []
        agent.conversation = {}
        agent.logger = _Logger()
        agent.get_event = lambda: Event(
            name,
            "此时",
            "研究论文",
            address=["the Ville", "学院", "图书馆", "桌子"],
        )
        agent.completion = response
        agent.schedule_chat = lambda *_args, **_kwargs: None
        return agent

    clock = _Clock()

    def initiator_response(purpose, *_args, **_kwargs):
        """为本测试模块封装 ``initiator_response`` 辅助步骤，减少重复的场景搭建代码。"""
        return {
            "decide_chat": True,
            "summarize_relation": "同学",
            "generate_chat": "要一起讨论论文吗？",
            "summarize_chats": "两人讨论论文",
        }[purpose]

    def responder_response(purpose, *_args, **_kwargs):
        """为本测试模块封装 ``responder_response`` 辅助步骤，减少重复的场景搭建代码。"""
        return {
            "summarize_relation": "同学",
            "generate_chat": "好，我们交换一下资料。",
            "decide_chat_terminate": True,
        }[purpose]

    initiator = build_agent("克劳斯", "resident-005", initiator_response)
    responder = build_agent("阿伊莎", "resident-024", responder_response)
    responder.conversation = initiator.conversation
    focus = {"events": [], "thoughts": []}

    assert initiator._chat_with(responder, focus) is True
    result = initiator.drain_result_events()
    assert len(result) == 1
    assert result[0]["kind"] == "conversation"
    assert result[0]["participants"] == ("resident-005", "resident-024")
    assert result[0]["messages"] == (
        ("克劳斯", "要一起讨论论文吗？"),
        ("阿伊莎", "好，我们交换一下资料。"),
    )
