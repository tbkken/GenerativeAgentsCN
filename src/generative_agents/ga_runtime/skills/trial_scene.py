"""Build an identity-bound initial scene without starting the scheduler."""
from __future__ import annotations
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.schemas.engine import get_algorithm_profile
from generative_agents.ga_protocol.schemas.manifests import validate_package_path
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_runtime.capabilities.server import SimulationAPI
from generative_agents.ga_runtime.engine.configuration import ConfigAdapter
from generative_agents.ga_runtime.engine.context import SimulationClock, SimulationContext
from generative_agents.ga_runtime.engine.context import SnapshotSkillInstructionRepository
from generative_agents.ga_runtime.engine.iteration import IterationContext
from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.lifecycle.package import load_experiment_directory
from generative_agents.ga_runtime.memory.stream import FileMemoryStream


@dataclass(slots=True)
class TrialScene:
    interfaces: SimulationAPI
    iteration: IterationContext
    memory: FileMemoryStream
    initial_memory: dict
    experiment_id: str


def build_trial_scene(root: Path, request: dict, *, paths, attempt_id, control, recorder, required_interfaces) -> TrialScene | None:
    relative = validate_package_path(request["experiment_directory"])
    scene_root = checked_package_path(root / relative)
    scene_root.relative_to(root.resolve())
    experiment = load_experiment_directory(scene_root)
    definition = experiment.definition
    agent_key = str(request.get("agent_key") or "")
    if not any(agent.agent_key == agent_key and agent.enabled for agent in definition.agents):
        raise SkillTrialError("SKILL_AGENT_NOT_IN_EXPERIMENT", "请选择当前实验中已启用的 Agent。")
    if definition.world.definition.get("environment_type") == "spatial_3d":
        if required_interfaces:
            scene_id = definition.world.definition["simworld_scene_id"]
            raise SkillTrialError("SKILL_REQUIRES_LIVE_3D_RUN",
                f"场景 {scene_id} 的临时试运行没有远端世界。请在正式 3D 实验中运行 "
                f"{agent_key} 的 Skill，以使用 {', '.join(required_interfaces)}。")
        return None
    clock = SimulationClock(definition.simulation.start_time)
    logger = logging.LoggerAdapter(logging.getLogger("generative_agents.skill_trial"), {})
    memory = FileMemoryStream(paths.root / "memory", run_id=paths.run_id,
                              attempt_id=attempt_id, clock=lambda: clock.get_date(), logger=logger)
    initial_memory = memory.begin_step(1, clock.current)
    context = SimulationContext(
        run_id=paths.run_id, attempt_id=attempt_id,
        experiment_id=UUID(experiment.manifest.experiment.experiment_id),
        definition_hash=experiment.root_sha256,
        algorithm=get_algorithm_profile(definition.engine.algorithm_version),
        clock=clock, random=random.Random(definition.simulation.random_seed), paths=paths,
        skills=SnapshotSkillInstructionRepository(experiment.skill_snapshot,
                                                 brain=experiment.skill_registry.brain_skill),
        models=None, control=control, logger=logger, memory_stream=memory,
        execution_trace=recorder.execution, metadata={"skill_trial": True},
    )
    config = ConfigAdapter().game_config(definition)
    config["storage_root"] = str(paths.root / "storage")
    game = Game(config, {}, context=context)
    agent = game.get_agent(agent_key)
    tile = agent.get_tile()
    iteration = IterationContext(
        run_id=paths.run_id, attempt_id=attempt_id, agent_key=agent_key, agent_name=agent.name,
        step_no=1, total_steps=definition.simulation.max_steps, now=clock.current,
        stride_minutes=definition.simulation.stride_minutes, coord=tuple(agent.coord) if agent.coord is not None else None,
        address=tuple(tile.get_address()) if tile is not None else (),
        spatial_semantics=tuple(dict(item) for item in getattr(tile, "spatial_semantics", ())),
        environment_type=game.environment_type, space_id=game.space_id, presentation=game.presentation,
        space_description=game.world_definition.get("description", ""),
        variables={
            "currently": agent.profile.currently,
            "profile": dict(agent.profile.config),
            "current_action": agent.get_event().to_dict(),
            "known_spatial_memory": {"tree": agent.spatial.tree, "named_addresses": agent.spatial.address} if agent.spatial is not None else {},
            "trial": {"initial_scene": True, "world_commit_enabled": False},
        },
    )
    return TrialScene(SimulationAPI(game, iteration, memory_stream=memory), iteration,
                      memory, initial_memory, str(context.experiment_id))
