"""基础能力回归测试：覆盖 ``test_agent_social_identity`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import random
from types import MappingProxyType, SimpleNamespace

from generative_agents.ga_runtime.engine.actor import ActorState as Agent
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.memory.event import Event




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






def test_speak_uses_stable_keys_and_rejects_an_unrelated_identity():
    from tests.runtime.test_brain_capability_runtime import _server, _Agent
    server = _server()
    game = Game.__new__(Game)
    game.__dict__.update(server.game.__dict__)
    game.context = SimpleNamespace(run_id=server.iteration.run_id)
    game._conversation_threads = {}
    game._open_conversation_by_participants = {}
    game._conversation_sequence = 0
    game.agents['resident-024'] = _Agent()
    game.agents['resident-024'].name = '阿伊莎'
    game.agent_keys_by_name['阿伊莎'] = 'resident-024'
    server.game = game
    result = server.call('world-act', {'action_type': 'SPEAK', 'participant_agent_keys': ['阿伊莎'], 'message': '一起讨论吗？'})
    assert result['isError'] is False
    assert server.action.arguments['participant_agent_keys'] == ['resident-024']
    committed = game.record_conversation_message('agent-1', ('resident-024',))
    assert committed['participants'] == ('agent-1', 'resident-024')
