"""把可重建查询投影回退到已验证的恢复检查点。"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock
from sqlalchemy import delete, select, update

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunArtifact,
    RunConversation,
    RunConversationParticipant,
    RunDomainEvent,
    RunDomainEventAgent,
    RunEvent,
    RunMemoryEvent,
    RunMessage,
    RunModelTraceCursor,
    RunRelationshipEdge,
    RunResultSummary,
    RunScheduleRevision,
    RunStep,
    RunStepEffect,
)
from generative_agents.status import ArtifactState

from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .context import RunPaths
from .frame_store import FrameStore, StoredFrame
from .results import StepResult
from .sqlite_result_projector import SqliteResultProjector
from .trace_projector import ModelTraceProjector


class RunProjectionRewinder:
    """从不可变帧重建指定步骤之前的全部可变结果视图。"""

    _PROJECTION_MODELS = (
        RunStepEffect,
        RunDomainEventAgent,
        RunConversationParticipant,
        RunMessage,
        RunDomainEvent,
        RunConversation,
        RunMemoryEvent,
        RunScheduleRevision,
        RunRelationshipEdge,
        RunAgentStep,
        RunAgentSummary,
        RunStep,
        RunResultSummary,
    )

    def __init__(self, database: Database, *, var_dir: str | Path):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self._database = database
        self._var_dir = Path(var_dir).resolve()

    def rewind(self, run_id: str, boundary: int) -> int:
        """执行 `RunProjectionRewinder` 的`rewind`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            boundary: 读取、恢复或提交时采用的已验证步骤边界。 类型：`int`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。

        说明:
            恢复点之后的帧、检查点和投影必须作为一个逻辑单元回退，避免不同存储层指向不同仿真步。
        """
        if boundary < 0:
            raise ValueError("recovery boundary must not be negative")
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        paths.ensure()
        with (
            FileLock(str(paths.worker_lock), timeout=5),
            FileLock(str(paths.artifact_lock), timeout=5),
        ):
            with self._database.session_factory() as session:
                run = session.get(Run, run_id)
                if run is None:
                    raise RuntimeError("run does not exist")
                if run.slot_no is not None or run.current_attempt_id is not None:
                    raise RuntimeError("cannot rewind an active Run")
                if boundary != run.recoverable_step or boundary > run.completed_steps:
                    raise RuntimeError(
                        "rewind boundary is not the durable Run boundary"
                    )
                summary = session.get(RunResultSummary, run_id)
                old_version = summary.result_version if summary else 0

            orphan_batch = paths.orphaned / f"rewind-{uuid4()}"
            self._quarantine_newer_frames(paths, boundary, orphan_batch / "frames")
            self._select_checkpoint(paths, boundary, orphan_batch / "checkpoints")

            with self._database.session_factory.begin() as session:
                for model in self._PROJECTION_MODELS:
                    session.execute(delete(model).where(model.run_id == run_id))
                run = session.get(Run, run_id)
                run.completed_steps = 0
                run.recoverable_step = 0
                run.virtual_time = None
                session.execute(
                    update(RunArtifact)
                    .where(
                        RunArtifact.run_id == run_id,
                        RunArtifact.state == ArtifactState.READY,
                    )
                    .values(state=ArtifactState.STALE.value)
                )

            store = FrameStore(paths)
            projector = SqliteResultProjector(self._database, var_dir=self._var_dir)
            for step_no in range(1, boundary + 1):
                document = store.read_document(step_no)
                result = StepResult.from_dict(document["result"])
                path = store.path_for(step_no)
                frame = StoredFrame(
                    path=path,
                    sha256=self._sha256(path),
                    created=False,
                )
                checkpoint_path = (
                    paths.checkpoints / f"step-{boundary:06d}"
                    if step_no == boundary
                    else None
                )
                projector.commit_step(
                    result,
                    frame=frame,
                    checkpoint_path=checkpoint_path,
                    allow_reconcile=True,
                )

            # 轨迹游标在投影回退后继续保留，因为只追加 JSONL 与用量聚合仍然有效。
            # RunStep 重建完成后，再按步骤精确对账用量总计。
            with self._database.session_factory() as session:
                trace_cursors = list(
                    session.scalars(
                        select(RunModelTraceCursor)
                        .where(RunModelTraceCursor.run_id == run_id)
                        .order_by(RunModelTraceCursor.attempt_id)
                    )
                )
                trace_inputs = [
                    (cursor.attempt_id, cursor.relative_path)
                    for cursor in trace_cursors
                ]
            trace_projector = ModelTraceProjector(self._database, var_dir=self._var_dir)
            for attempt_id, relative_path in trace_inputs:
                trace_projector.project(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    relative_path=relative_path,
                )

            now = datetime.now(timezone.utc)
            with self._database.session_factory.begin() as session:
                summary = session.get(RunResultSummary, run_id)
                result_version = old_version + 1
                if summary is not None:
                    summary.result_version = max(summary.result_version, result_version)
                    summary.updated_at = now
                    result_version = summary.result_version
                run = session.get(Run, run_id)
                run.completed_steps = boundary
                run.recoverable_step = boundary
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="result_rewound",
                        payload_json={
                            "available_step": boundary,
                            "recoverable_step": boundary,
                            "result_version": result_version,
                        },
                        created_at=now,
                    )
                )
            return result_version

    @staticmethod
    def _quarantine_newer_frames(paths, boundary: int, destination: Path) -> None:
        """执行`quarantine``newer`帧集合的内部处理，供当前模块或类复用。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。
            boundary: 读取、恢复或提交时采用的已验证步骤边界。 类型：`int`。
            destination: 移动、复制、导出或写入操作的目标位置。 类型：`Path`。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        frame_root = paths.frames.resolve()
        for frame in sorted(paths.frames.glob("step-*.json.gz")):
            try:
                step_no = int(frame.name[5:11])
            except ValueError:
                continue
            if step_no <= boundary:
                continue
            resolved = frame.resolve()
            if resolved.parent != frame_root or frame.is_symlink():
                raise RuntimeError("unsafe future frame path")
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / frame.name
            if target.exists():
                raise RuntimeError("future frame was already quarantined")
            os.replace(resolved, target)

    @staticmethod
    def _select_checkpoint(paths, boundary: int, destination: Path) -> None:
        """执行`select`检查点的内部处理，供当前模块或类复用。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。
            boundary: 读取、恢复或提交时采用的已验证步骤边界。 类型：`int`。
            destination: 移动、复制、导出或写入操作的目标位置。 类型：`Path`。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        reader = CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )
        if boundary > 0:
            reader.select_for_recovery(boundary, orphan_root=destination)
            return
        with reader.access():
            checkpoint_root = paths.checkpoints.resolve()
            for checkpoint in sorted(paths.checkpoints.glob("step-*")):
                resolved = checkpoint.resolve()
                if resolved.parent != checkpoint_root or checkpoint.is_symlink():
                    raise RuntimeError("unsafe checkpoint path")
                destination.mkdir(parents=True, exist_ok=True)
                os.replace(resolved, destination / checkpoint.name)
            (paths.checkpoints / "LATEST").unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        """执行`sha256`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            返回处理后的文本或稳定标识。
        """
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
