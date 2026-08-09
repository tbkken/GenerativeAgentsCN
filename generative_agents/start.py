"""Run-scoped simulation loop and import-safe CLI adapter.

The Web worker constructs dependencies from a verified Run manifest. This
module deliberately has no bootstrap catalog or display-name path fallback.
"""

from __future__ import annotations

import argparse
import copy
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from generative_agents.modules.game import Game
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.commit import FileStepCommitter
from generative_agents.runtime.context import RunPaths, SimulationContext
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.manifest import RunManifestStore
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.runtime.file_result_projector import FileResultProjector
from generative_agents.runtime.results import StepResultBuilder


class StepCommitter(Protocol):
    def commit(self, result, *, force_checkpoint: bool): ...


def apply_checkpoint_state(config: dict, state: Mapping) -> dict:
    """Overlay verified mutable Agent state without changing Revision input."""
    restored = copy.deepcopy(config)
    checkpoint_agents = state.get("agents")
    if not isinstance(checkpoint_agents, Mapping):
        raise ValueError("checkpoint state must contain an agents mapping")
    configured_keys = set(restored.get("agents", {}))
    checkpoint_keys = set(checkpoint_agents)
    if checkpoint_keys != configured_keys:
        raise ValueError(
            "checkpoint agent keys do not match the published Revision"
        )

    def overlay(target: dict, source: Mapping) -> None:
        for key, value in source.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                overlay(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    for agent_key, agent_state in checkpoint_agents.items():
        if not isinstance(agent_state, Mapping):
            raise ValueError(f"checkpoint agent state is invalid: {agent_key}")
        overlay(restored["agents"][agent_key], agent_state)
    return restored


@dataclass(slots=True)
class SimulationRunner:
    context: SimulationContext
    game: Game
    committer: StepCommitter
    checkpoint_interval_steps: int = 1
    completed_steps: int = 0
    agent_status: dict = field(init=False)

    def __post_init__(self) -> None:
        if self.checkpoint_interval_steps < 1:
            raise ValueError("checkpoint_interval_steps must be positive")
        self.agent_status = {
            agent_key: {"coord": tuple(agent.coord), "path": tuple(agent.path or ())}
            for agent_key, agent in self.game.agents.items()
        }

    def run(self, steps: int, *, stride_minutes: int) -> int:
        if steps < 1 or stride_minutes < 1:
            raise ValueError("steps and stride_minutes must be positive")
        self.game.reset_game()
        for offset in range(steps):
            if self.context.control.cancel_requested or self.context.control.pause_requested:
                break
            step_no = self.completed_steps + 1
            builder = StepResultBuilder(
                run_id=self.context.run_id,
                attempt_id=self.context.attempt_id,
                step_no=step_no,
                virtual_time=self.context.clock.get_date(),
            )
            collector = StepResultCollector(
                builder,
                name_to_key={agent.name: key for key, agent in self.game.agents.items()},
            )
            for agent_key, status in self.agent_status.items():
                agent = self.game.get_agent(agent_key)
                from_coord = tuple(agent.coord)
                outcome = self.game.agent_think(agent_key, status)
                collector.capture_agent(agent_key, agent, from_coord, outcome)
                observed_path = outcome.get("plan", {}).get("path") or ()
                status["coord"] = (
                    tuple(observed_path[-1]) if observed_path else tuple(agent.coord)
                )
                status["path"] = tuple()
            result = collector.freeze()
            terminal_boundary = (
                offset == steps - 1
                or self.context.control.pause_requested
                or self.context.control.cancel_requested
            )
            force_checkpoint = (
                terminal_boundary or step_no % self.checkpoint_interval_steps == 0
            )
            self.committer.commit(result, force_checkpoint=force_checkpoint)
            self.completed_steps = step_no
            if not terminal_boundary:
                self.context.clock.forward(stride_minutes)
        return self.completed_steps


def build_file_committer(context: SimulationContext, game: Game) -> FileStepCommitter:
    checkpoint = CheckpointBundleWriter(
        context.paths,
        lambda _result: CheckpointSnapshot(
            state=game.snapshot_state(),
            conversation=game.conversation,
            storage_exporters=game.storage_exporters(),
        ),
    )
    return FileStepCommitter(
        FrameStore(context.paths),
        FileResultProjector(context.paths),
        checkpoint,
    )


def build_runner(
    context: SimulationContext,
    definition,
    *,
    embedding_api_key: str = "",
    checkpoint_state: Mapping | None = None,
    checkpoint_conversation: Mapping | None = None,
    storage_root: str | Path | None = None,
) -> SimulationRunner:
    """Build the old domain engine from only a verified Revision and Run context."""

    config = ConfigAdapter().game_config(
        definition, embedding_api_key=embedding_api_key
    )
    if checkpoint_state is not None:
        config = apply_checkpoint_state(config, checkpoint_state)
    if storage_root is not None:
        config["storage_root"] = str(Path(storage_root))
    game = Game(
        config,
        copy.deepcopy(checkpoint_conversation or {}),
        context=context,
    )
    if checkpoint_state is not None:
        game.restore_runtime_state(dict(checkpoint_state))
    return SimulationRunner(
        context=context,
        game=game,
        committer=build_file_committer(context, game),
        checkpoint_interval_steps=definition.simulation.checkpoint_interval_steps,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run one isolated experiment worker")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--stride-minutes", type=int, required=True)
    parser.add_argument(
        "--runner-factory",
        required=True,
        help="Dotted callable module:function that verifies the manifest and returns SimulationRunner",
    )
    return parser


def _load_factory(path: str):
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("runner factory must use module:function syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("runner factory is not callable")
    return factory


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    paths = RunPaths.under(args.data_root, args.run_id)
    manifest = RunManifestStore(paths).load_verified()
    runner = _load_factory(args.runner_factory)(
        paths=paths,
        manifest=manifest,
        attempt_id=args.attempt_id,
    )
    if not isinstance(runner, SimulationRunner):
        raise TypeError("runner factory must return SimulationRunner")
    runner.run(args.steps, stride_minutes=args.stride_minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
