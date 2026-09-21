from __future__ import annotations

import pytest

from generative_agents.ga_protocol.schemas.experiment import AgentDefinition
from generative_agents.ga_studio.resources.catalog import StudioAgentDefinition


def _agent_payload() -> dict:
    return {
        "agent_key": "visual-scale-agent",
        "name": "Visual Scale Agent",
        "coord": [0, 0],
        "scratch": {
            "age": 30,
            "innate": "",
            "learned": "",
            "lifestyle": "",
            "daily_plan": "",
        },
    }


def test_agent_display_tiles_is_optional_and_bounded_across_public_and_run_models():
    public_payload = _agent_payload()
    public_payload.pop("coord")
    public = StudioAgentDefinition.model_validate({**public_payload, "sprite_display_tiles": 2.0})
    run = AgentDefinition.model_validate({**_agent_payload(), "sprite_display_tiles": 2.0})
    assert public.sprite_display_tiles == run.sprite_display_tiles == 2.0
    assert StudioAgentDefinition.model_validate(public_payload).sprite_display_tiles is None
    assert AgentDefinition.model_validate(_agent_payload()).sprite_display_tiles is None

    with pytest.raises(ValueError):
        StudioAgentDefinition.model_validate({**public_payload, "sprite_display_tiles": 0.4})
    with pytest.raises(ValueError):
        AgentDefinition.model_validate({**_agent_payload(), "sprite_display_tiles": 6.1})
