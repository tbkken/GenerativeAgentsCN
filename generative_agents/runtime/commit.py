"""统一约束帧、检查点与查询投影的提交顺序。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .frame_store import FrameStore, StoredFrame
from .results import StepResult


class CheckpointWriter(Protocol):
    def write(self, result: StepResult, frame: StoredFrame) -> Path:
        """执行 `CheckpointWriter` 的`write`操作。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
            frame: 当前仿真步已经落盘且内容不可变的帧记录。 类型：`StoredFrame`。

        返回:
            返回目标文件或目录路径。
        """
        ...


class StepProjection(Protocol):
    def commit_step(
        self,
        result: StepResult,
        *,
        frame: StoredFrame,
        checkpoint_path: Path | None,
    ) -> int:
        """原子提交单步查询投影，并返回更新后的结果版本号。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
            frame: 当前仿真步已经落盘且内容不可变的帧记录。 类型：`StoredFrame`。
            checkpoint_path: 当前步骤对应的检查点目录；未生成检查点时为 `None`。 类型：`Path | None`。

        返回:
            返回计算得到的整数值或版本号。
        """
        ...


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    step_no: int
    frame_path: Path
    frame_sha256: str
    checkpoint_path: Path | None
    result_version: int


class FileStepCommitter:
    """工作进程中唯一允许推进查询投影可用步骤的接口。"""

    def __init__(
        self,
        frame_store: FrameStore,
        projection: StepProjection,
        checkpoint_writer: CheckpointWriter | None = None,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            frame_store: 传入当前算法的帧`store`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`FrameStore`。
            projection: 负责把不可变步骤结果写入查询模型的投影实现。 类型：`StepProjection`。
            checkpoint_writer: 传入当前算法的检查点`writer`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`CheckpointWriter | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        self._frame_store = frame_store
        self._projection = projection
        self._checkpoint_writer = checkpoint_writer

    def commit(self, result: StepResult, *, force_checkpoint: bool) -> CommitReceipt:
        """按照持久化顺序提交当前仿真步，并返回提交凭据。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
            force_checkpoint: 是否无视常规间隔，为当前步骤强制生成检查点。 类型：`bool`。

        返回:
            返回 `CommitReceipt` 类型的处理结果。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        frame = self._frame_store.write(result)
        checkpoint_path = None
        if force_checkpoint:
            if self._checkpoint_writer is None:
                raise RuntimeError("force_checkpoint requires a checkpoint writer")
            checkpoint_path = self._checkpoint_writer.write(result, frame)
        result_version = self._projection.commit_step(
            result,
            frame=frame,
            checkpoint_path=checkpoint_path,
        )
        return CommitReceipt(
            step_no=result.step_no,
            frame_path=frame.path,
            frame_sha256=frame.sha256,
            checkpoint_path=checkpoint_path,
            result_version=result_version,
        )
