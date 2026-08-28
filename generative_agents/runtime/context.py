"""单次 Run 私有的运行上下文和文件系统边界。

这里集中保存虚拟时钟、随机源、控制信号、目录、模型与 Skill 依赖，目的是让多个
Run 可以在同一进程中交错执行而不共享可变全局状态。
"""

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

from generative_agents.skills import SkillRegistry

from .algorithm import AlgorithmProfile


@dataclass(slots=True)
class SimulationClock:
    """A worker-local virtual clock; it never uses module global state."""

    current: datetime

    def __post_init__(self) -> None:
        # Old snapshots predate the aware-time contract. Their naive wall time
        # is deterministic simulation UTC, never the host's local timezone.
        """完成数据类初始化后的规范化与不变量校验。

        返回:
            无返回值。
        """
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            self.current = self.current.replace(tzinfo=timezone.utc)

    def advance(self, minutes: int) -> datetime:
        """执行 `SimulationClock` 的`advance`操作。

        参数:
            minutes: 需要推进、等待或分配的虚拟分钟数。 类型：`int`。

        返回:
            返回 `datetime` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if minutes <= 0:
            raise ValueError("minutes must be greater than zero")
        self.current += timedelta(minutes=minutes)
        return self.current

    # Compatibility methods used by the gradually extracted simulation domain.
    # They are instance methods so two runs can safely interleave in one test process.
    def forward(self, offset: int) -> None:
        """执行 `SimulationClock` 的`forward`操作。

        参数:
            offset: 从结果集或字节流起点跳过的数量。 类型：`int`。

        返回:
            无返回值。
        """
        self.advance(offset)

    def get_date(self, date_format: str = "") -> datetime | str:
        """获取`date`。

        参数:
            date_format: 日期时间解析或输出采用的格式字符串。 类型：`str`。 默认值：`''`。

        返回:
            返回处理后的文本或稳定标识。
        """
        return self.current.strftime(date_format) if date_format else self.current

    def get_delta(
        self,
        start: datetime,
        end: datetime | None = None,
        mode: str = "minute",
    ):
        """获取`delta`。

        参数:
            start: 处理区间的起始位置或起始时间。 类型：`datetime`。
            end: 处理区间的结束位置或结束时间；是否包含由当前接口约定。 类型：`datetime | None`。 默认值：`None`。
            mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 类型：`str`。 默认值：`'minute'`。

        返回:
            返回函数计算得到的结果。
        """
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
        """执行 `SimulationClock` 的`daily``format`操作。

        返回:
            返回处理后的文本或稳定标识。
        """
        return self.current.strftime("%A %B %d")

    @staticmethod
    def get_weekday(value: datetime) -> str:
        """获取`weekday`。

        参数:
            value: 当前操作使用的`value`。 类型：`datetime`。

        返回:
            返回处理后的文本或稳定标识。
        """
        return ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[
            value.weekday()
        ]

    def daily_format_cn(self) -> str:
        """执行 `SimulationClock` 的`daily``format``cn`操作。

        返回:
            返回处理后的文本或稳定标识。
        """
        return f"{self.current:%Y年%m月%d日}（{self.get_weekday(self.current)}）"

    def time_format_cn(self, value: datetime) -> str:
        """执行 `SimulationClock` 的`time``format``cn`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`datetime`。

        返回:
            返回处理后的文本或稳定标识。
        """
        return f"{value:%Y年%m月%d日}（{self.get_weekday(value)}）{value:%H:%M}"

    def daily_duration(self, mode: str = "minute"):
        """执行 `SimulationClock` 的`daily``duration`操作。

        参数:
            mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 类型：`str`。 默认值：`'minute'`。

        返回:
            返回函数计算得到的结果。
        """
        duration = self.current.hour % 24
        if mode == "hour":
            return duration
        duration = duration * 60 + self.current.minute
        if mode == "minute":
            return duration
        return timedelta(minutes=duration)

    def daily_time(self, duration: int) -> datetime:
        """执行 `SimulationClock` 的`daily``time`操作。

        参数:
            duration: 行为、对话或日程项占用的虚拟时间长度。 类型：`int`。

        返回:
            返回 `datetime` 类型的处理结果。
        """
        base = self.current.replace(hour=0, minute=0, second=0, microsecond=0)
        return base + timedelta(minutes=duration)

    @property
    def mode(self) -> str:
        """执行 `SimulationClock` 的`mode`操作。

        返回:
            返回处理后的文本或稳定标识。
        """
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
        """执行 `RunControl` 的`request``pause`操作。

        返回:
            无返回值。
        """
        self._pause_requested.set()

    def request_cancel(self) -> None:
        """执行 `RunControl` 的`request``cancel`操作。

        返回:
            无返回值。
        """
        self._cancel_requested.set()

    @property
    def pause_requested(self) -> bool:
        """暂停`requested`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        return self._pause_requested.is_set()

    @property
    def cancel_requested(self) -> bool:
        """取消`requested`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        return self._cancel_requested.is_set()


@dataclass(frozen=True, slots=True)
class RunPaths:
    """All writable paths owned by exactly one run UUID."""

    root: Path
    run_id: UUID

    @classmethod
    def under(cls, data_root: str | Path, run_id: UUID) -> "RunPaths":
        """执行 `RunPaths` 的`under`操作。

        参数:
            data_root: 数据使用的根目录路径。 类型：`str | Path`。
            run_id: 仿真运行的唯一标识。 类型：`UUID`。

        返回:
            返回目标文件或目录路径。
        """
        root = Path(data_root).resolve() / "runs" / str(run_id)
        return cls(root=root, run_id=run_id)

    @property
    def manifest(self) -> Path:
        """执行 `RunPaths` 的运行清单操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "manifest.json"

    @property
    def frames(self) -> Path:
        """执行 `RunPaths` 的帧集合操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "frames"

    @property
    def checkpoints(self) -> Path:
        """执行 `RunPaths` 的检查点集合操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "checkpoints"

    @property
    def logs(self) -> Path:
        """执行 `RunPaths` 的`logs`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "logs"

    @property
    def model_calls(self) -> Path:
        """执行 `RunPaths` 的模型`calls`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.traces

    @property
    def traces(self) -> Path:
        """执行 `RunPaths` 的`traces`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "traces"

    @property
    def artifacts(self) -> Path:
        """执行 `RunPaths` 的产物集合操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "artifacts"

    @property
    def orphaned(self) -> Path:
        """执行 `RunPaths` 的`orphaned`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "orphaned"

    @property
    def worker_lock(self) -> Path:
        """执行 `RunPaths` 的工作进程`lock`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "worker.lock"

    @property
    def checkpoint_lock(self) -> Path:
        """执行 `RunPaths` 的检查点`lock`操作。

        返回:
            返回目标文件或目录路径。
        """

        return self.root / "checkpoint.lock"

    @property
    def artifact_lock(self) -> Path:
        """执行 `RunPaths` 的产物`lock`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.root / "artifact.lock"

    @property
    def temporary(self) -> Path:
        """执行 `RunPaths` 的`temporary`操作。

        返回:
            返回目标文件或目录路径。
        """
        return self.frames / ".tmp"

    def ensure(self) -> None:
        """执行 `RunPaths` 的`ensure`操作。

        返回:
            无返回值。
        """
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
    """按稳定键读取本次运行已经固定版本的 Skill 指令。"""

    def get(self, key: str) -> str:
        """执行 `SkillInstructionRepository` 的`get`操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。
        """
        ...

    def revision(self, key: str) -> str:
        """执行 `SkillInstructionRepository` 的修订版本操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。
        """
        ...


class ModelRegistry(Protocol):
    """按用途取得当前 Run 的模型实例，而不是读取进程级默认模型。"""

    def get(self, purpose: str) -> Any:
        """执行 `ModelRegistry` 的`get`操作。

        参数:
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`str`。

        返回:
            返回 `Any` 类型的处理结果。
        """
        ...


class PassiveSkillExecutor(Protocol):
    """执行由世界事件触发、无需智能体主动选择的被动 Skill。"""

    def run(
        self,
        skill_name: str,
        input_text: str,
        *,
        context: Mapping[str, Any],
    ) -> Any:
        """执行当前组件负责的完整流程，并返回本次执行结果。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`Mapping[str, Any]`。

        返回:
            返回 `Any` 类型的处理结果。
        """
        ...


@dataclass(frozen=True, slots=True)
class FileSkillInstructionRepository:
    """Resolve every Agent prompt from its real file-backed ``SKILL.md``."""

    registry: SkillRegistry
    brain: str = "stanford-town-brain"

    def __post_init__(self) -> None:
        """完成数据类初始化后的规范化与不变量校验。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        brain = self.registry.get(self.brain)
        if brain.kind != "brain":
            raise ValueError(f"Configured brain is not a brain Skill: {self.brain}")

    def get(self, key: str) -> str:
        """执行 `FileSkillInstructionRepository` 的`get`操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。
        """
        return self.registry.prompt(key)

    def revision(self, key: str) -> str:
        """执行 `FileSkillInstructionRepository` 的修订版本操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。
        """
        return self.registry.get(str(key).replace("_", "-")).revision


@dataclass(frozen=True, slots=True)
class SnapshotSkillInstructionRepository:
    """Read prompt regions from the immutable Skill bundle in one Run manifest."""

    skills: Mapping[str, Mapping[str, Any]]
    brain: str = "stanford-town-brain"

    def __post_init__(self) -> None:
        """完成数据类初始化后的规范化与不变量校验。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        normalized = {str(key).replace("_", "-") for key in self.skills}
        missing: set[str] = set()
        if self.brain not in normalized:
            missing.add(self.brain)
        if missing:
            raise ValueError(
                "run Skill bundle is incomplete: " + ", ".join(sorted(missing))
            )

    def get(self, key: str) -> str:
        """执行 `SnapshotSkillInstructionRepository` 的`get`操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            KeyError: 当必需的键或映射项不存在时抛出。
        """
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
        return (
            markdown[frontmatter_end + 4 :].strip()
            if frontmatter_end >= 0
            else markdown
        )

    def revision(self, key: str) -> str:
        """执行 `SnapshotSkillInstructionRepository` 的修订版本操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            KeyError: 当必需的键或映射项不存在时抛出。
        """
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
    brain_runtime: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
