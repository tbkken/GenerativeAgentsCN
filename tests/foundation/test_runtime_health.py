"""Regressions for current shared kernel and Studio surfaces."""

from generative_agents.ga_runtime.supervision.health import runtime_health_issues

class _BrokenGame:
    def __init__(self):
        self.ready = True

    def agent_think(self):
        return self.record_interval


class _HealthyGame:
    def __init__(self):
        self.ready = True

    def agent_think(self):
        return self.ready


def test_preflight_detects_an_agent_think_attribute_missing_from_constructor():
    assert runtime_health_issues(_BrokenGame) == [
        {
            "code": "RUNTIME_INSTANCE_ATTRIBUTE_UNINITIALIZED",
            "path": "runtime.Game.record_interval",
            "message": "运行内核会读取未由构造函数初始化的状态：Game.record_interval",
        }
    ]


def test_current_game_core_passes_runtime_health_contract():
    assert runtime_health_issues(_HealthyGame) == []
    assert runtime_health_issues() == []
