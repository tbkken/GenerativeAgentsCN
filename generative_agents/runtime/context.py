"""Per-run runtime context and filesystem boundaries."""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Protocol
from uuid import UUID

from generative_agents.config import WorkflowDefinition
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS

from .algorithm import AlgorithmProfile
from .workflow_engine import LLMNodeHandler, WorkflowExecutionResult, WorkflowExecutor


@dataclass(slots=True)
class SimulationClock:
    """A worker-local virtual clock; it never uses module global state."""

    current: datetime

    def __post_init__(self) -> None:
        # Old snapshots predate the aware-time contract. Their naive wall time
        # is deterministic simulation UTC, never the host's local timezone.
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            self.current = self.current.replace(tzinfo=timezone.utc)

    def advance(self, minutes: int) -> datetime:
        if minutes <= 0:
            raise ValueError("minutes must be greater than zero")
        self.current += timedelta(minutes=minutes)
        return self.current

    # Compatibility methods used by the gradually extracted simulation domain.
    # They are instance methods so two runs can safely interleave in one test process.
    def forward(self, offset: int) -> None:
        self.advance(offset)

    def get_date(self, date_format: str = "") -> datetime | str:
        return self.current.strftime(date_format) if date_format else self.current

    def get_delta(
        self,
        start: datetime,
        end: datetime | None = None,
        mode: str = "minute",
    ):
        end = end or self.current
        seconds = (end - start).total_seconds()
        if mode == "second":
            return seconds
        if mode == "minute":
            return round(seconds / 60)
        if mode == "hour":
            return round(seconds / 3600)
        return end - start

    def daily_format(self) -> str:
        return self.current.strftime("%A %B %d")

    @staticmethod
    def get_weekday(value: datetime) -> str:
        return ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[
            value.weekday()
        ]

    def daily_format_cn(self) -> str:
        return f"{self.current:%Y年%m月%d日}（{self.get_weekday(self.current)}）"

    def time_format_cn(self, value: datetime) -> str:
        return f"{value:%Y年%m月%d日}（{self.get_weekday(value)}）{value:%H:%M}"

    def daily_duration(self, mode: str = "minute"):
        duration = self.current.hour % 24
        if mode == "hour":
            return duration
        duration = duration * 60 + self.current.minute
        if mode == "minute":
            return duration
        return timedelta(minutes=duration)

    def daily_time(self, duration: int) -> datetime:
        base = self.current.replace(hour=0, minute=0, second=0, microsecond=0)
        return base + timedelta(minutes=duration)

    @property
    def mode(self) -> str:
        return "on_time"


@dataclass(slots=True)
class RunControl:
    """Cooperative process-local control flags.

    Durable state remains in the run repository. These flags only make the
    worker react promptly between step boundaries.
    """

    _pause_requested: threading.Event = field(default_factory=threading.Event)
    _cancel_requested: threading.Event = field(default_factory=threading.Event)

    def request_pause(self) -> None:
        self._pause_requested.set()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def pause_requested(self) -> bool:
        return self._pause_requested.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()


@dataclass(frozen=True, slots=True)
class RunPaths:
    """All writable paths owned by exactly one run UUID."""

    root: Path
    run_id: UUID

    @classmethod
    def under(cls, data_root: str | Path, run_id: UUID) -> "RunPaths":
        root = Path(data_root).resolve() / "runs" / str(run_id)
        return cls(root=root, run_id=run_id)

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def model_calls(self) -> Path:
        return self.traces

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def orphaned(self) -> Path:
        return self.root / "orphaned"

    @property
    def worker_lock(self) -> Path:
        return self.root / "worker.lock"

    @property
    def checkpoint_lock(self) -> Path:
        """Cross-process lock protecting published checkpoint bundles."""

        return self.root / "checkpoint.lock"

    @property
    def artifact_lock(self) -> Path:
        return self.root / "artifact.lock"

    @property
    def temporary(self) -> Path:
        return self.frames / ".tmp"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.frames,
            self.checkpoints,
            self.logs,
            self.traces,
            self.artifacts,
            self.orphaned,
            self.temporary,
        ):
            path.mkdir(parents=True, exist_ok=True)


class PromptRepository(Protocol):
    def get(self, key: str) -> str: ...


class ModelRegistry(Protocol):
    def get(self, purpose: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class MappingPromptRepository:
    """Immutable prompt lookup materialized from a Revision snapshot."""

    prompts: Mapping[str, str]

    def get(self, key: str) -> str:
        try:
            return self.prompts[key]
        except KeyError as exc:
            raise KeyError(f"prompt is not present in run manifest: {key}") from exc


@dataclass(frozen=True, slots=True)
class WorkflowPromptRepository:
    """Prompt lookup plus executable graph pinned by one immutable Run manifest."""

    prompts: Mapping[str, str]
    workflows: Mapping[str, WorkflowDefinition]
    function_sources: Mapping[str, str] = field(default_factory=dict)
    trace_handler: Callable[[Mapping[str, Any]], None] | None = field(
        default=None, repr=False, compare=False
    )
    _prompt_nodes: Mapping[str, tuple[str, str]] = field(init=False, repr=False)
    _prompt_configs: Mapping[str, Mapping[str, Any]] = field(init=False, repr=False)
    _executor: WorkflowExecutor = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        placements: dict[str, tuple[str, str]] = {}
        configs: dict[str, Mapping[str, Any]] = {}
        for workflow_key, workflow in self.workflows.items():
            for node in workflow.nodes:
                if node.kind != "llm" or node.prompt_key is None:
                    continue
                if node.prompt_key in placements:
                    raise ValueError(
                        f"prompt is placed in multiple workflow nodes: {node.prompt_key}"
                    )
                placements[node.prompt_key] = (workflow_key, node.node_id)
                configs[node.prompt_key] = node.config
        missing = REQUIRED_PROMPT_KEYS - set(placements)
        if missing:
            raise ValueError(
                "run manifest workflows do not place prompts: " + ", ".join(sorted(missing))
            )
        object.__setattr__(self, "_prompt_nodes", placements)
        object.__setattr__(self, "_prompt_configs", configs)
        object.__setattr__(
            self,
            "_executor",
            WorkflowExecutor(
                self.workflows,
                function_sources=self.function_sources,
                trace_handler=self.trace_handler,
            ),
        )

    def get(self, key: str) -> str:
        if key not in self._prompt_nodes:
            raise KeyError(f"prompt is not placed in a run workflow: {key}")
        try:
            return self.prompts[key]
        except KeyError as exc:
            raise KeyError(f"prompt is not present in run manifest: {key}") from exc

    def node_for_prompt(self, key: str) -> tuple[str, str]:
        try:
            return self._prompt_nodes[key]
        except KeyError as exc:
            raise KeyError(f"prompt is not placed in a run workflow: {key}") from exc

    def config_for_prompt(self, key: str) -> Mapping[str, Any]:
        try:
            return self._prompt_configs[key]
        except KeyError as exc:
            raise KeyError(f"prompt is not placed in a run workflow: {key}") from exc

    def execution_mode_for_prompt(self, key: str) -> str:
        workflow_key, _node_id = self.node_for_prompt(key)
        return self.workflows[workflow_key].execution_mode

    def execute_prompt(
        self,
        key: str,
        step_context: Mapping[str, Any],
        *,
        llm_handler: LLMNodeHandler,
        state: MutableMapping[str, Any],
        invocation_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Execute the complete selector-routed graph for one Agent Prompt call."""

        workflow_key, _node_id = self.node_for_prompt(key)
        workflow = self.workflows[workflow_key]
        if workflow.execution_mode != "prompt_router":
            raise RuntimeError(
                f"workflow {workflow_key} is not a runnable Prompt router"
            )
        return self._executor.execute(
            workflow_key,
            {"step_context": dict(step_context)},
            llm_handler=llm_handler,
            runtime_context=step_context,
            state=state,
            invocation_id=invocation_id,
        )

    def invoke_prompt_result(
        self,
        key: str,
        value: Any,
        *,
        runtime_context: Mapping[str, Any],
        state: MutableMapping[str, Any],
        invocation_id: str | None = None,
    ) -> Any:
        """Pass a legacy Agent prompt result through its real graph hook."""

        workflow_key, node_id = self.node_for_prompt(key)
        return self._executor.execute_prompt_hook(
            workflow_key,
            node_id,
            value,
            runtime_context=runtime_context,
            state=state,
            invocation_id=invocation_id,
        ).value

    def execute_workflow(
        self,
        workflow_key: str,
        inputs: Mapping[str, Any],
        *,
        llm_handler: LLMNodeHandler,
        runtime_context: Mapping[str, Any] | None = None,
        state: MutableMapping[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Native entry point for scenario capabilities that do not use Agent hooks."""

        return self._executor.execute(
            workflow_key,
            inputs,
            llm_handler=llm_handler,
            runtime_context=runtime_context,
            state=state,
            invocation_id=invocation_id,
        )


@dataclass(slots=True)
class SimulationContext:
    """Dependencies that are private to a single worker/run."""

    run_id: UUID
    experiment_id: UUID
    revision_id: UUID
    attempt_id: UUID
    definition_hash: str
    algorithm: AlgorithmProfile
    clock: SimulationClock
    random: random.Random
    paths: RunPaths
    prompts: PromptRepository
    models: ModelRegistry
    control: RunControl
    logger: logging.LoggerAdapter
    metadata: Mapping[str, Any] = field(default_factory=dict)
