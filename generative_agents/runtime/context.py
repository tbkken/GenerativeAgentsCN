"""Per-run runtime context and filesystem boundaries."""

from __future__ import annotations

import logging
import random
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import UUID

from generative_agents.config.schema import REQUIRED_ATOMIC_SKILLS
from generative_agents.skills import SkillRegistry

from .algorithm import AlgorithmProfile


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


class SkillInstructionRepository(Protocol):
    def get(self, key: str) -> str: ...

    def revision(self, key: str) -> str: ...


class ModelRegistry(Protocol):
    def get(self, purpose: str) -> Any: ...


class PassiveSkillExecutor(Protocol):
    def run(
        self,
        skill_name: str,
        input_text: str,
        *,
        context: Mapping[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class FileSkillInstructionRepository:
    """Resolve every Agent prompt from its real file-backed ``SKILL.md``."""

    registry: SkillRegistry
    brain: str = "stanford-town-brain"

    def __post_init__(self) -> None:
        brain = self.registry.get(self.brain)
        if brain.kind != "brain":
            raise ValueError(f"Configured brain is not a brain Skill: {self.brain}")
        missing = []
        for key in REQUIRED_ATOMIC_SKILLS:
            try:
                self.registry.get(key)
            except ValueError:
                missing.append(key)
        if missing:
            raise ValueError(
                "brain Skill set is missing atomic Skills: " + ", ".join(sorted(missing))
            )

    def get(self, key: str) -> str:
        return self.registry.prompt(key)

    def revision(self, key: str) -> str:
        return self.registry.get(str(key).replace("_", "-")).revision


@dataclass(frozen=True, slots=True)
class SnapshotSkillInstructionRepository:
    """Read prompt regions from the immutable Skill bundle in one Run manifest."""

    skills: Mapping[str, Mapping[str, Any]]
    brain: str = "stanford-town-brain"

    def __post_init__(self) -> None:
        normalized = {str(key).replace("_", "-") for key in self.skills}
        missing = {
            key.replace("_", "-") for key in REQUIRED_ATOMIC_SKILLS
        } - normalized
        if self.brain not in normalized:
            missing.add(self.brain)
        if missing:
            raise ValueError(
                "run Skill bundle is incomplete: " + ", ".join(sorted(missing))
            )

    def get(self, key: str) -> str:
        name = str(key).replace("_", "-")
        try:
            markdown = str(self.skills[name]["markdown"])
        except KeyError as exc:
            raise KeyError(f"Skill is not present in run manifest: {name}") from exc
        match = re.search(
            r"<!--\s*PROMPT:START\s*-->\s*(.*?)\s*<!--\s*PROMPT:END\s*-->",
            markdown,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()
        frontmatter_end = markdown.find("\n---", 4)
        return markdown[frontmatter_end + 4 :].strip() if frontmatter_end >= 0 else markdown

    def revision(self, key: str) -> str:
        name = str(key).replace("_", "-")
        try:
            return str(self.skills[name]["revision"])
        except KeyError as exc:
            raise KeyError(f"Skill is not present in run manifest: {name}") from exc


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
    skills: SkillInstructionRepository
    models: ModelRegistry
    control: RunControl
    logger: logging.LoggerAdapter
    passive_skills: PassiveSkillExecutor | None = None
    memory_stream: Any | None = None
    skill_mcp: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
