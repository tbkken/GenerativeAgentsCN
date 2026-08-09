"""Simulation game assembled entirely from one Run context and manifest snapshot."""

from __future__ import annotations

import copy
from pathlib import Path

from generative_agents.modules import utils
from generative_agents.modules.agent import Agent
from generative_agents.modules.maze import Maze
from generative_agents.runtime.context import SimulationContext


def _as_tuple_tree(value):
    """Restore tuple-shaped state after a JSON checkpoint round-trip."""
    if isinstance(value, list):
        return tuple(_as_tuple_tree(item) for item in value)
    return value


class Game:
    """A run-local aggregate; no process registry or bootstrap file reads."""

    def __init__(
        self,
        config: dict,
        conversation: dict,
        *,
        context: SimulationContext,
    ):
        self.context = context
        self.name = str(context.run_id)
        self.record_interval = config.get(
            "record_interval_minutes", config.get("record_iterval", 30)
        )
        self.logger = context.logger
        maze_definition = config.get("maze") or config.get("world")
        if not isinstance(maze_definition, dict):
            raise ValueError("run manifest must contain an inline maze/world definition")
        self.maze = Maze(
            copy.deepcopy(maze_definition), self.logger, context.random
        )
        self.conversation = conversation
        self.agents: dict[str, Agent] = {}
        agent_base = copy.deepcopy(config.get("agent_base", {}))
        storage_root = Path(
            config.get("storage_root", context.paths.root / "storage")
        )
        agents = config.get("agents", {})
        if not isinstance(agents, dict):
            raise TypeError("runtime agent configuration must be keyed by agent_key")
        for agent_key, definition in agents.items():
            if "config_path" in definition:
                raise ValueError(
                    "runtime agent definitions must be materialized; config_path is legacy-only"
                )
            agent_config = utils.update_dict(
                copy.deepcopy(agent_base), copy.deepcopy(definition)
            )
            agent_config["agent_key"] = agent_key
            agent_config["storage_root"] = str(storage_root / agent_key)
            embedding_config = agent_config.get("associate", {}).get("embedding")
            if isinstance(embedding_config, dict):
                embedding_config["_control"] = context.control
                embedding_config["_logger"] = context.logger
            self.agents[agent_key] = Agent(
                agent_config,
                self.maze,
                self.conversation,
                self.logger,
                clock=context.clock,
                random_source=context.random,
                prompts=context.prompts,
                models=context.models,
                model_trace=context.metadata.get("model_trace"),
                algorithm=context.algorithm,
            )

    def get_agent(self, agent_key: str) -> Agent:
        return self.agents[agent_key]

    def agent_think(self, agent_key: str, status: dict) -> dict:
        agent = self.get_agent(agent_key)
        plan = agent.think(status, self.agents)
        info = {
            "currently": agent.scratch.currently,
            "associate": agent.associate.abstract(),
            "concepts": {concept.node_id: concept.abstract() for concept in agent.concepts},
            "chats": [
                {"name": "self" if name == agent.name else name, "chat": chat}
                for name, chat in agent.chats
            ],
            "action": agent.action.abstract(),
            "schedule": agent.schedule.abstract(),
            "address": agent.get_tile().get_address(as_list=False),
        }
        elapsed = self.context.clock.daily_duration() - agent.last_record
        info["record"] = elapsed > self.record_interval
        if info["record"]:
            agent.last_record = self.context.clock.daily_duration()
        if agent.llm_available():
            info["llm"] = agent._llm.get_summary()
        title = "{}.summary @ {}".format(
            agent_key, self.context.clock.get_date("%Y%m%d-%H:%M:%S")
        )
        self.logger.info("\n{}\n{}\n".format(utils.split_line(title), agent))
        return {"plan": plan, "info": info, "events": agent.drain_result_events()}

    def reset_game(self) -> None:
        for agent_key, agent in self.agents.items():
            agent.reset()
            self.logger.info(
                "\n{}\n{}\n".format(utils.split_line(f"{agent_key}.reset"), agent)
            )

    def snapshot_state(self) -> dict:
        return {
            "agents": {
                agent_key: {
                    **agent.to_dict(),
                    "coord": list(agent.coord),
                    "path": [list(coord) for coord in agent.path or []],
                }
                for agent_key, agent in self.agents.items()
            },
            "virtual_time": self.context.clock.get_date().isoformat(),
            # random.Random state contains only JSON-safe scalar/tuple values.
            # Checkpoint serialization turns tuples into lists; restore_runtime_state
            # converts the shape back before calling setstate().
            "rng_state": self.context.random.getstate(),
        }

    def restore_runtime_state(self, snapshot: dict) -> None:
        """Restore run-local deterministic state from a verified checkpoint."""
        rng_state = snapshot.get("rng_state")
        if rng_state is None:
            raise ValueError("checkpoint is missing rng_state")
        self.context.random.setstate(_as_tuple_tree(rng_state))

    def storage_exporters(self):
        return {
            agent_key: agent.associate.export_storage
            for agent_key, agent in self.agents.items()
        }


def create_game(config, conversation, *, context: SimulationContext) -> Game:
    return Game(config, conversation, context=context)
