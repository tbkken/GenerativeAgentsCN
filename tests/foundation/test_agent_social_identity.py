from __future__ import annotations

import random
from types import MappingProxyType, SimpleNamespace

from generative_agents.modules.agent import Agent
from generative_agents.modules.game import Game
from generative_agents.modules.memory.event import Event


def test_reaction_resolves_event_display_name_to_agent():
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
    observed_rosters = []

    class _Agent:
        name = "克劳斯"
        last_record = 0
        concepts = []
        chats = []
        scratch = SimpleNamespace(currently="研究论文")
        associate = SimpleNamespace(abstract=lambda: {})
        action = SimpleNamespace(abstract=lambda: {})
        schedule = SimpleNamespace(abstract=lambda: {})

        def think(self, _status, roster):
            observed_rosters.append(roster)
            return {"name": self.name, "path": [], "emojis": {}}

        def get_tile(self):
            return SimpleNamespace(get_address=lambda **_kwargs: "the Ville:图书馆")

        def llm_available(self):
            return False

        def drain_result_events(self):
            return ()

        def __str__(self):
            return self.name

    agent = _Agent()
    game = Game.__new__(Game)
    game.agents = {"resident-005": agent}
    game.agents_by_name = MappingProxyType({"克劳斯": agent})
    game.context = SimpleNamespace(
        clock=SimpleNamespace(
            daily_duration=lambda: 0,
            get_date=lambda _fmt=None: "20260214-10:00:00",
        )
    )
    game.record_interval = 30
    game.logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)

    game.agent_think("resident-005", {"coord": (0, 0), "path": ()})

    assert game.get_agent("resident-005") is agent
    assert len(observed_rosters) == 1
    assert tuple(observed_rosters[0]) == ("克劳斯",)
    assert "resident-005" not in observed_rosters[0]


def test_find_path_excludes_tile_occupied_by_named_agent_event():
    occupied = (1, 0)
    available = (2, 0)

    class _Tile:
        def __init__(self, events, address=None):
            self._events = events
            self._address = address or ["the Ville", "其他区域"]

        def get_events(self):
            return self._events

        def get_address(self):
            return self._address

    class _Maze:
        def get_address_tiles(self, _address):
            return {occupied, available}

        def tile_at(self, coord):
            if tuple(coord) == occupied:
                return _Tile([Event("阿伊莎", "此时", "阅读")])
            return _Tile([])

        def find_path(self, source, target):
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
    class _Schedule:
        daily_schedule = [{"describe": "研究论文"}]

    class _Associate:
        def retrieve_chats(self, _name):
            return []

    class _Logger:
        def info(self, *_args, **_kwargs):
            pass

    class _Clock:
        def daily_duration(self, mode=None):
            return 10 if mode == "hour" else 600

        def get_date(self, fmt=None):
            if fmt:
                return "20260214-10:00"
            return SimpleNamespace()

    def build_agent(name, agent_key, response):
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
        return {
            "decide_chat": True,
            "summarize_relation": "同学",
            "generate_chat": "要一起讨论论文吗？",
            "summarize_chats": "两人讨论论文",
        }[purpose]

    def responder_response(purpose, *_args, **_kwargs):
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
