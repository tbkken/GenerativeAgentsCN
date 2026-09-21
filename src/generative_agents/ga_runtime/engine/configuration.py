"""Materialize protocol inputs without embedding a cognition pipeline."""
from __future__ import annotations
import copy
from generative_agents.ga_protocol.schemas.experiment import ExperimentDefinition

class ConfigAdapter:
    """No file, database or environment reads are permitted here."""
    def game_config(self, definition: ExperimentDefinition, *, embedding_api_key: str = "") -> dict:
        agents = {}
        for agent in definition.agents:
            if not agent.enabled:
                continue
            agents[agent.agent_key] = {
                "name": agent.name, "coord": list(agent.coord),
                "currently": agent.currently,
                "scratch": agent.scratch.model_dump(mode="json"),
                "spatial": agent.spatial.model_dump(mode="json"),
                "percept": {"mode": agent.perception.mode,
                            "vision_r": agent.perception.vision_radius,
                            "att_bandwidth": agent.perception.attention_bandwidth},
            }
        return {"maze": copy.deepcopy(definition.world.definition), "agents": agents}
