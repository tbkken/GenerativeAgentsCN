"""Atomic and immutable storage for complete per-step result frames."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .context import RunPaths
from .results import StepResult


class FrameConflictError(RuntimeError):
    """Raised when a committed step is rewritten with different content."""


@dataclass(frozen=True, slots=True)
class StoredFrame:
    """已经原子写入磁盘的步骤帧及其内容摘要。"""

    path: Path
    sha256: str
    created: bool


class FrameStore:
    """按 Run/步骤身份写入不可变帧，并拒绝同键不同内容的覆盖。"""

    SCHEMA_VERSION = 1

    def __init__(self, paths: RunPaths):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。

        返回:
            无返回值。
        """
        self._paths = paths
        self._paths.ensure()

    def path_for(self, step_no: int) -> Path:
        """执行 `FrameStore` 的路径`for`操作。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            返回目标文件或目录路径。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if step_no < 1:
            raise ValueError("step_no must be greater than zero")
        return self._paths.frames / f"step-{step_no:06d}.json.gz"

    def write(self, result: StepResult) -> StoredFrame:
        """执行 `FrameStore` 的`write`操作。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。

        返回:
            返回 `StoredFrame` 类型的处理结果。

        异常:
            FrameConflictError: 当底层操作报告该异常条件时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if result.run_id != self._paths.run_id:
            raise ValueError("result run_id does not own this FrameStore")
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "result": result.to_dict(),
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        target = self.path_for(result.step_no)

        if target.exists():
            existing = target.read_bytes()
            if existing != compressed:
                raise FrameConflictError(
                    f"step {result.step_no} already has different immutable content"
                )
            return StoredFrame(path=target, sha256=digest, created=False)

        temporary = self._paths.temporary / f"frame-{result.step_no}-{uuid4()}.tmp"
        try:
            with temporary.open("xb") as file_handle:
                file_handle.write(compressed)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredFrame(path=target, sha256=digest, created=True)

    def read_document(self, step_no: int) -> dict:
        """读取`document`。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        target = self.path_for(step_no)
        with gzip.open(target, "rt", encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        if document.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported frame schema at {target}")
        result = document.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"missing result object at {target}")
        if result.get("run_id") != str(self._paths.run_id):
            raise ValueError(f"frame run_id mismatch at {target}")
        if result.get("step_no") != step_no:
            raise ValueError(f"frame step_no mismatch at {target}")
        return document

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """执行`fsync``directory`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            无返回值。
        """
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
