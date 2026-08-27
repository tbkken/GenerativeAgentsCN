"""Validated checkpoint inventory, safe details and bounded previews."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import Run, RunStep
from generative_agents.runtime.checkpoint import (
    CheckpointBundleWriter,
    CheckpointSnapshot,
)
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.model_trace import redact_error
from generative_agents.status import RESUMABLE_RUN_STATUSES

from .byte_windows import read_utf8_window
from .errors import ServiceError, not_found
from .run_storage import RunStorageBoundary


_CHECKPOINT_NAME = re.compile(r"^step-([0-9]{6})$")
_PREVIEW_FILES = {
    "bundle": "bundle.json",
    "state": "state.json",
    "conversation": "conversation.json",
}


class CheckpointService:
    """列出、校验、预览和导出属于指定 Run 的检查点。"""

    def __init__(self, database: Database, *, var_dir: str | Path):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self._database = database
        self._boundary = RunStorageBoundary(var_dir)

    def list_checkpoints(self, run_id: str) -> dict[str, Any]:
        """查询检查点集合。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        run, paths, database_markers = self._context(run_id)
        reader = self._reader(paths)
        with reader.access():
            physical: dict[int, Path] = {}
            if paths.checkpoints.exists():
                for candidate in paths.checkpoints.iterdir():
                    match = _CHECKPOINT_NAME.fullmatch(candidate.name)
                    if match and (candidate.is_dir() or candidate.is_symlink()):
                        physical[int(match.group(1))] = candidate
            steps = set(database_markers) | set(physical)
            if run.recoverable_step > 0:
                steps.add(run.recoverable_step)
            items = [
                self._item(
                    run,
                    paths,
                    step_no,
                    database_marker=database_markers.get(step_no),
                    physical_path=physical.get(step_no),
                    reader=reader,
                )
                for step_no in sorted(steps, reverse=True)
            ]
        can_resume = any(item["resumable"] for item in items)
        return {
            "run_id": run.id,
            "run_status": run.status,
            "recoverable_step": run.recoverable_step,
            "can_resume": can_resume,
            "items": items,
        }

    def detail(self, run_id: str, step_no: int) -> dict[str, Any]:
        """执行 `CheckpointService` 的`detail`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        run, paths, database_markers = self._context(run_id)
        reader = self._reader(paths)
        with reader.access():
            return self._detail_locked(
                run,
                paths,
                database_markers,
                step_no,
                reader=reader,
            )

    def _detail_locked(
        self,
        run: Run,
        paths: RunPaths,
        database_markers: dict[int, RunStep],
        step_no: int,
        *,
        reader: CheckpointBundleWriter,
    ) -> dict[str, Any]:
        """执行`detail``locked`的内部处理，供当前模块或类复用。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
            database_markers: 传入当前算法的`database``markers`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[int, RunStep]`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            reader: 提供受控读取或反序列化能力的组件。 类型：`CheckpointBundleWriter`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        item = self._item(
            run,
            paths,
            step_no,
            database_marker=database_markers.get(step_no),
            physical_path=paths.checkpoints / f"step-{step_no:06d}",
            reader=reader,
        )
        if item["status"] == "NOT_FOUND":
            raise not_found("checkpoint", f"{run.id}:{step_no}")
        if item["status"] == "PRUNED":
            raise ServiceError(
                "CHECKPOINT_PRUNED",
                "检查点已按保留策略清理",
                status_code=410,
                details={"step_no": step_no},
            )
        if not item["validated"]:
            raise ServiceError(
                "CHECKPOINT_INVALID",
                "检查点完整性校验失败",
                status_code=409,
                details={"step_no": step_no, "validation": item["validation"]},
            )
        checkpoint = paths.checkpoints / f"step-{step_no:06d}"
        bundle = self._read_json(checkpoint / "bundle.json")
        state = self._read_json(checkpoint / "state.json")
        conversation = self._read_json(checkpoint / "conversation.json")
        agents = state.get("agents", {}) if isinstance(state, dict) else {}
        if not isinstance(agents, dict):
            agents = {}
        agent_items = [
            self._agent_summary(str(agent_key), value)
            for agent_key, value in sorted(
                agents.items(), key=lambda pair: str(pair[0])
            )
        ]
        conversation_items = self._conversation_items(conversation)
        files = [self._file_summary(entry) for entry in bundle.get("files", [])]
        storage = self._storage_summary(files)
        return {
            **item,
            "run_id": run.id,
            "bundle": {
                "schema_version": bundle.get("bundle_schema_version"),
                "run_id": bundle.get("run_id"),
                "attempt_id": bundle.get("attempt_id"),
                "step_no": bundle.get("step_no"),
                "virtual_time": bundle.get("virtual_time"),
                "frame_sha256": bundle.get("frame_sha256"),
                "bundle_sha256": item["bundle_sha256"],
            },
            "agent_state": {
                "count": len(agent_items),
                "items": agent_items[:200],
                "truncated": len(agent_items) > 200,
            },
            "conversations": {
                "count": len(conversation_items),
                "items": conversation_items[:100],
                "truncated": len(conversation_items) > 100,
            },
            "storage": storage,
            "files": files,
            "preview_sections": sorted(_PREVIEW_FILES),
        }

    def preview(
        self,
        run_id: str,
        step_no: int,
        section: str,
        *,
        cursor: int = 0,
        limit_bytes: int = 32_768,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """执行 `CheckpointService` 的`preview`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            section: 需要读取或修改的草稿配置区域名称。 类型：`str`。
            cursor: 分页游标；为空时从结果集起点开始读取。 类型：`int`。 默认值：`0`。
            limit_bytes: 本次最多读取或返回的字节数；UTF-8 边界修正后可能略少。 类型：`int`。 默认值：`32768`。
            file_id: `file`的唯一标识。 类型：`str | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if section not in _PREVIEW_FILES:
            raise ServiceError(
                "CHECKPOINT_PREVIEW_SECTION_INVALID",
                "检查点预览分区不受支持",
                status_code=422,
                details={"allowed": sorted(_PREVIEW_FILES)},
            )
        run, paths, markers = self._context(run_id)
        reader = self._reader(paths)
        with reader.access():
            # Validate and consume under the same lock.  Otherwise retention
            # could remove a verified member between these two operations.
            self._detail_locked(run, paths, markers, step_no, reader=reader)
            target = paths.checkpoints / f"step-{step_no:06d}" / _PREVIEW_FILES[section]
            window = read_utf8_window(
                target,
                cursor=cursor,
                limit_bytes=limit_bytes,
                expected_file_id=file_id,
                missing_code="CHECKPOINT_MEMBER_MISSING",
                truncated_code="CHECKPOINT_MEMBER_TRUNCATED",
                rotated_code="CHECKPOINT_MEMBER_CHANGED",
                encoding_code="CHECKPOINT_MEMBER_ENCODING_INVALID",
            )
        return {
            "run_id": run_id,
            "step_no": step_no,
            "section": section,
            "cursor": window.start_cursor,
            "next_cursor": None if window.eof else window.next_cursor,
            "content": window.content,
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "eof": window.eof,
        }

    def validate_for_export(self, run_id: str, step_no: int) -> dict[str, Any]:
        """校验`for``export`。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        detail = self.detail(run_id, step_no)
        if not detail["validated"]:
            raise ServiceError(
                "CHECKPOINT_INVALID", "检查点完整性校验失败", status_code=409
            )
        return detail

    def _context(self, run_id: str) -> tuple[Run, RunPaths, dict[int, RunStep]]:
        """执行运行上下文的内部处理，供当前模块或类复用。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。

        返回:
            返回目标文件或目录路径。
        """
        try:
            parsed_run_id = UUID(run_id)
        except ValueError as exc:
            raise not_found("run", run_id) from exc
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            markers = {
                row.step_no: row
                for row in session.scalars(
                    select(RunStep).where(
                        RunStep.run_id == run_id, RunStep.checkpoint.is_(True)
                    )
                )
            }
            root = self._boundary.run_root(run)
            session.expunge(run)
            for row in markers.values():
                session.expunge(row)
        return run, RunPaths(root=root, run_id=parsed_run_id), markers

    def _item(
        self,
        run: Run,
        paths: RunPaths,
        step_no: int,
        *,
        database_marker: RunStep | None,
        physical_path: Path | None,
        reader: CheckpointBundleWriter,
    ) -> dict[str, Any]:
        """执行`item`的内部处理，供当前模块或类复用。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            database_marker: 传入当前算法的`database``marker`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunStep | None`。
            physical_path: `physical`对应的文件系统路径。 类型：`Path | None`。
            reader: 提供受控读取或反序列化能力的组件。 类型：`CheckpointBundleWriter`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        base = {
            "step_no": step_no,
            "database_marker": database_marker is not None,
            "retained": False,
            "validated": False,
            "status": "PRUNED",
            "attempt_id": database_marker.attempt_id if database_marker else None,
            "virtual_time": (
                database_marker.virtual_time.isoformat() if database_marker else None
            ),
            "bundle_sha256": None,
            "size_bytes": 0,
            "file_count": 0,
            "resumable": False,
            "validation": None,
        }
        if physical_path is None:
            if step_no == run.recoverable_step:
                base["status"] = "INVALID"
                base["validation"] = {
                    "code": "CHECKPOINT_AUTHORIZED_BUNDLE_MISSING",
                    "reason": "database-authorized recovery checkpoint is not retained",
                }
            elif database_marker is None:
                base["status"] = "NOT_FOUND"
            return base
        if physical_path.is_symlink():
            base["retained"] = True
            base["status"] = "INVALID"
            base["validation"] = {
                "code": "CHECKPOINT_SYMLINK_REJECTED",
                "reason": "checkpoint directory must not be a symbolic link",
            }
            return base
        if not physical_path.exists():
            if step_no == run.recoverable_step:
                base["status"] = "INVALID"
                base["validation"] = {
                    "code": "CHECKPOINT_AUTHORIZED_BUNDLE_MISSING",
                    "reason": "database-authorized recovery checkpoint is not retained",
                }
            elif database_marker is None:
                base["status"] = "NOT_FOUND"
            return base
        base["retained"] = True
        try:
            stored = reader.validate(physical_path)
            bundle = self._read_json(physical_path / "bundle.json")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            base["status"] = "INVALID"
            base["validation"] = {
                "code": "CHECKPOINT_VALIDATION_FAILED",
                "reason": self._validation_reason(exc, paths.root),
            }
            return base
        files = bundle.get("files", [])
        base.update(
            {
                "validated": True,
                "status": (
                    "RECOVERABLE" if step_no == run.recoverable_step else "RETAINED"
                ),
                "attempt_id": bundle.get("attempt_id"),
                "virtual_time": bundle.get("virtual_time"),
                "bundle_sha256": stored.bundle_sha256,
                "size_bytes": sum(
                    int(item.get("size", 0)) for item in files if isinstance(item, dict)
                )
                + (physical_path / "bundle.json").stat().st_size,
                "file_count": len(files) + 1,
                "resumable": (
                    step_no == run.recoverable_step
                    and run.status in RESUMABLE_RUN_STATUSES
                ),
                "validation": {"code": "VALID", "reason": None},
            }
        )
        return base

    @staticmethod
    def _reader(paths: RunPaths) -> CheckpointBundleWriter:
        """执行`reader`的内部处理，供当前模块或类复用。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。

        返回:
            返回计算得到的整数值或版本号。
        """
        return CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )

    @staticmethod
    def _read_json(path: Path) -> Any:
        """读取`json`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            返回 `Any` 类型的处理结果。
        """
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @classmethod
    def _agent_summary(cls, agent_key: str, value: Any) -> dict[str, Any]:
        """执行智能体摘要的内部处理，供当前模块或类复用。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            value: 当前操作使用的`value`。 类型：`Any`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        state = value if isinstance(value, dict) else {}
        action = state.get("action") if isinstance(state.get("action"), dict) else {}
        schedule = state.get("schedule") or state.get("daily_schedule") or []
        return {
            "agent_key": agent_key,
            "coord": cls._safe_value(state.get("coord")),
            "currently": cls._safe_value(state.get("currently")),
            "action": cls._safe_value(
                {
                    key: action.get(key)
                    for key in ("event", "address", "emoji", "start_time", "duration")
                    if key in action
                }
            ),
            "schedule_item_count": len(schedule) if isinstance(schedule, list) else 0,
        }

    @classmethod
    def _conversation_items(cls, value: Any) -> list[Any]:
        """执行`conversation``items`的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`Any`。

        返回:
            返回按接口约定组织的结果集合。
        """
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict):
            candidate = value.get("items", value.get("conversations", []))
            items = candidate if isinstance(candidate, list) else []
        else:
            items = []
        return [cls._safe_value(item) for item in items]

    @staticmethod
    def _file_summary(value: Any) -> dict[str, Any]:
        """执行`file`摘要的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`Any`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        item = value if isinstance(value, dict) else {}
        return {
            "path": str(item.get("path", "")),
            "size_bytes": int(item.get("size", 0)),
            "sha256": item.get("sha256"),
        }

    @staticmethod
    def _storage_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
        """执行存储摘要的内部处理，供当前模块或类复用。

        参数:
            files: 传入当前算法的`files`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[dict[str, Any]]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for item in files:
            parts = Path(item["path"]).parts
            if len(parts) < 3 or parts[0] != "storage":
                continue
            key = (parts[1], parts[2])
            group = groups.setdefault(
                key,
                {
                    "agent_key": parts[1],
                    "index_type": parts[2],
                    "file_count": 0,
                    "size_bytes": 0,
                },
            )
            group["file_count"] += 1
            group["size_bytes"] += item["size_bytes"]
        return {"groups": list(groups.values()), "group_count": len(groups)}

    @classmethod
    def _safe_value(cls, value: Any, *, depth: int = 0) -> Any:
        """执行`safe``value`的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`Any`。
            depth: 树遍历、递归展开或引用解析允许到达的最大层级。 类型：`int`。 默认值：`0`。

        返回:
            返回 `Any` 类型的处理结果。
        """
        if depth > 4:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            output = {}
            for key, item in list(value.items())[:100]:
                folded = str(key).casefold()
                if any(
                    token in folded
                    for token in ("embedding", "vector", "api_key", "secret", "token")
                ):
                    continue
                output[str(key)] = cls._safe_value(item, depth=depth + 1)
            return output
        if isinstance(value, list):
            return [cls._safe_value(item, depth=depth + 1) for item in value[:100]]
        if isinstance(value, str):
            return redact_error(value[:2_000])
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return str(value)[:2_000]

    @staticmethod
    def _validation_reason(exc: Exception, run_root: Path) -> str:
        """执行`validation``reason`的内部处理，供当前模块或类复用。

        参数:
            exc: 上游捕获的异常对象，用于分类、脱敏或转换错误信息。 类型：`Exception`。
            run_root: 运行使用的根目录路径。 类型：`Path`。

        返回:
            返回处理后的文本或稳定标识。
        """
        message = str(exc).replace(str(run_root), "[run]")
        return redact_error(f"{type(exc).__name__}: {message}")[:1_000]
