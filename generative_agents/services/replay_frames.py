"""Authoritative, cached verification of DB-owned committed frames."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import stat
import threading
from collections import OrderedDict
from pathlib import Path

from generative_agents.persistence.models import Run, RunStep
from generative_agents.runtime.results import StepResult

from .errors import ServiceError
from .run_storage import RunStorageBoundary


class VerifiedRunFrameReader:
    """Validate RunStep storage facts before exposing any Replay producer.

    Verification identities include the DB authority and append-irrelevant file
    stat facts.  The bounded result cache avoids retaining every decoded frame;
    the larger verification cache makes repeated manifests O(new/changed frames).
    """

    _lock = threading.RLock()
    _verified: OrderedDict[tuple, None] = OrderedDict()
    _results: OrderedDict[tuple, StepResult] = OrderedDict()
    _MAX_VERIFIED = 50_000
    _MAX_RESULTS = 512

    def __init__(self, var_dir: str | Path):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self._boundary = RunStorageBoundary(var_dir)

    def read(
        self, run: Run, row: RunStep, *, materialize: bool = True
    ) -> StepResult | None:
        """执行 `VerifiedRunFrameReader` 的`read`操作。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            row: 从数据库查询得到、待转换为服务响应的记录。 类型：`RunStep`。
            materialize: 是否读取并返回完整内容；关闭时只验证元数据和归属。 类型：`bool`。 默认值：`True`。

        返回:
            返回 `StepResult | None` 类型的处理结果。 没有可用结果时返回 `None`。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        try:
            with self._boundary.open_owned_binary(
                run, row.frame_path, area="frames"
            ) as (path, handle, opened_stat):
                return self._read_opened(
                    row,
                    path=path,
                    handle=handle,
                    opened_stat=opened_stat,
                    materialize=materialize,
                )
        except FileNotFoundError as exc:
            raise ServiceError(
                "REPLAY_FRAME_MISSING",
                "Replay 所需的提交帧不存在",
                status_code=410,
                details={"step_no": row.step_no},
            ) from exc

    def _read_opened(
        self,
        row: RunStep,
        *,
        path: Path,
        handle,
        opened_stat,
        materialize: bool,
    ) -> StepResult | None:
        """读取`opened`。

        参数:
            row: 从数据库查询得到、待转换为服务响应的记录。 类型：`RunStep`。
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            handle: 已经打开并由调用方负责生命周期的二进制文件句柄。
            opened_stat: 传入当前算法的`opened``stat`；其结构与有效范围由类型注解和调用协议共同限定。
            materialize: 是否读取并返回完整内容；关闭时只验证元数据和归属。 类型：`bool`。

        返回:
            返回 `StepResult | None` 类型的处理结果。 没有可用结果时返回 `None`。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if not stat.S_ISREG(opened_stat.st_mode):
            raise self._boundary._integrity_error()
        identity = (
            str(path),
            row.run_id,
            row.step_no,
            row.attempt_id,
            row.frame_path,
            row.frame_sha256,
            opened_stat.st_dev,
            opened_stat.st_ino,
            opened_stat.st_size,
            opened_stat.st_mtime_ns,
            opened_stat.st_ctime_ns,
        )
        with self._lock:
            cached_result = self._results.get(identity)
            verified = identity in self._verified
            if cached_result is not None:
                self._results.move_to_end(identity)
                self._verified.move_to_end(identity)
                return cached_result if materialize else None
            if verified and not materialize:
                self._verified.move_to_end(identity)
                return None

        if not verified:
            digest = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            if not hmac.compare_digest(digest.hexdigest(), row.frame_sha256):
                raise ServiceError(
                    "REPLAY_FRAME_INTEGRITY_ERROR",
                    "Replay 帧完整性校验失败",
                    status_code=500,
                    details={"step_no": row.step_no},
                )

        try:
            handle.seek(0)
            with gzip.GzipFile(fileobj=handle, mode="rb") as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as text_handle:
                    envelope = json.load(text_handle)
            if envelope.get("schema_version") != 1:
                raise ValueError("unsupported frame schema")
            result = StepResult.from_dict(envelope["result"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ServiceError(
                "REPLAY_FRAME_INVALID",
                "Replay 帧无法解析",
                status_code=500,
                details={"step_no": row.step_no},
            ) from exc
        if (
            str(result.run_id) != row.run_id
            or result.step_no != row.step_no
            or str(result.attempt_id) != row.attempt_id
        ):
            raise ServiceError(
                "REPLAY_FRAME_OWNERSHIP_INVALID",
                "Replay 帧归属不一致",
                status_code=500,
                details={"step_no": row.step_no},
            )

        with self._lock:
            self._verified[identity] = None
            self._verified.move_to_end(identity)
            while len(self._verified) > self._MAX_VERIFIED:
                expired, _ = self._verified.popitem(last=False)
                self._results.pop(expired, None)
            if materialize:
                self._results[identity] = result
                self._results.move_to_end(identity)
                while len(self._results) > self._MAX_RESULTS:
                    self._results.popitem(last=False)
        return result if materialize else None
