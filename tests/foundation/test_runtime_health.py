"""Preflight checks for deterministic failures in the simulation core."""

import pytest

from generative_agents.runtime.health import runtime_health_issues
from generative_agents.services import runs
from generative_agents.services.errors import ServiceError


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


def test_run_queue_rejects_a_runtime_health_failure(monkeypatch):
    issue = runtime_health_issues(_BrokenGame)[0]
    monkeypatch.setattr(runs, "runtime_health_issues", lambda: [issue])

    with pytest.raises(ServiceError) as caught:
        runs._assert_runtime_health()

    assert caught.value.code == "RUNTIME_PREFLIGHT_FAILED"
    assert caught.value.status_code == 422
    assert caught.value.details == {"errors": [issue]}
