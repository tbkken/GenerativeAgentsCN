"""Materialize protocol inputs without embedding a cognition pipeline."""
from __future__ import annotations
import copy
from generative_agents.ga_protocol.schemas.experiment import ExperimentDefinition

class ConfigAdapter:
    """No file, database or environment reads are permitted here."""
    def game_config(self, definition: ExperimentDefinition, *, embedding_api_key: str = "") -> dict:
        agents = {}
        social = definition.world.definition.get("environment_type") == "social_1d"
        ground = definition.world.definition.get("environment_type") == "spatial_3d"
        for agent in definition.agents:
            if not agent.enabled:
                continue
            agents[agent.agent_key] = {
                "name": agent.name,
                "currently": agent.currently,
                "scratch": agent.scratch.model_dump(mode="json"),
                "percept": {"mode": agent.perception.mode,
                            "vision_r": agent.perception.vision_radius,
                            "att_bandwidth": agent.perception.attention_bandwidth},
            }
            if ground:
                placement = {"spawn_anchor": agent.spawn_anchor, "yaw_deg": agent.yaw_deg}
            elif social:
                placement = {"space_id": agent.space_id}
            else:
                placement = {"coord": list(agent.coord), "spatial": agent.spatial.model_dump(mode="json")}
            agents[agent.agent_key].update(placement)
        return {"maze": copy.deepcopy(definition.world.definition), "agents": agents,
                "space_id": definition.world.world_key, "world_name": definition.world.world_name}
