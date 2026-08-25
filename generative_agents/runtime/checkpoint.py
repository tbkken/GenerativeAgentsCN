"""不可变检查点包的写入、校验、读取与保留管理。"""

from __future__ import annotations

import hashlib
import gzip
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from filelock import FileLock

from .context import RunPaths
from .frame_store import StoredFrame
from .results import StepResult


StorageExporter = Callable[[Path], None]
_SAFE_AGENT_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
_PRUNE_TOMBSTONE = re.compile(r"^\.prune-step-[0-9]{6}-[0-9a-f-]+\.tmp$")
_LOGGER = logging.getLogger(__name__)


def _canonical_json(value: Any) -> bytes:
    """执行`canonical``json`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`Any`。

    返回:
        返回 `bytes` 类型的处理结果。
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    """执行`sha256`的内部处理，供当前模块或类复用。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

    返回:
        返回处理后的文本或稳定标识。
    """
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    state: Mapping[str, Any]
    conversation: Mapping[str, Any]
    storage_exporters: Mapping[str, StorageExporter] = field(default_factory=dict)
    runtime_storage_exporters: Mapping[str, StorageExporter] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class StoredCheckpoint:
    path: Path
    bundle_sha256: str
    created: bool


class CheckpointConflictError(RuntimeError):
    pass


class CheckpointBundleWriter:
    """把检查点写入已验证目录，并原子推进 `LATEST` 指针。"""

    BUNDLE_SCHEMA_VERSION = 1

    def __init__(
        self,
        paths: RunPaths,
        snapshot_provider: Callable[[StepResult], CheckpointSnapshot],
        *,
        retention: int = 2,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。
            snapshot_provider: 在安全步骤边界生成当前运行快照的回调。 类型：`Callable[[StepResult], CheckpointSnapshot]`。
            retention: 需要保留的最新检查点、日志或记录数量。 类型：`int`。 默认值：`2`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if retention < 2:
            raise ValueError("checkpoint retention must be at least two")
        self._paths = paths
        self._snapshot_provider = snapshot_provider
        self._retention = retention
        self._paths.ensure()
        # 正确性优先于等待时限：大型检查点预览或导出仍在读取旧包时，
        # 不能因为抢锁超时而把一个本可持久化的仿真步判为失败。
        self._checkpoint_lock = FileLock(str(self._paths.checkpoint_lock), timeout=-1)

    @contextmanager
    def access(self):
        """串行化检查点的读取、发布与保留清理，避免跨进程竞争。

        返回:
            无返回值。

        说明:
            文件锁覆盖检查点目录的读取、临时写入、原子发布和过期清理，不能缩小到单次文件写操作。
        """

        with self._checkpoint_lock:
            yield

    def write(self, result: StepResult, frame: StoredFrame) -> Path:
        """执行 `CheckpointBundleWriter` 的`write`操作。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
            frame: 当前仿真步已经落盘且内容不可变的帧记录。 类型：`StoredFrame`。

        返回:
            返回目标文件或目录路径。

        异常:
            CheckpointConflictError: 当底层操作报告该异常条件时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        with self.access():
            if result.run_id != self._paths.run_id:
                raise ValueError("result run_id does not own this checkpoint writer")
            snapshot = self._snapshot_provider(result)
            target = self._paths.checkpoints / f"step-{result.step_no:06d}"
            if target.exists():
                existing = self.validate(target)
                self._advance_latest(target.name, existing.bundle_sha256)
                self._prune_old()
                return target

            temporary = self._paths.checkpoints / (
                f".step-{result.step_no:06d}-{uuid4()}.tmp"
            )
            temporary.mkdir(parents=False, exist_ok=False)
            try:
                self._write_bytes(
                    temporary / "state.json", _canonical_json(snapshot.state)
                )
                self._write_bytes(
                    temporary / "conversation.json",
                    _canonical_json(snapshot.conversation),
                )
                shutil.copyfile(frame.path, temporary / "frame.json.gz")
                self._fsync_file(temporary / "frame.json.gz")

                storage_root = temporary / "storage"
                for agent_key, exporter in sorted(snapshot.storage_exporters.items()):
                    if not _SAFE_AGENT_KEY.fullmatch(agent_key):
                        raise ValueError(
                            f"unsafe agent_key for storage path: {agent_key!r}"
                        )
                    destination = storage_root / agent_key / "associate"
                    destination.mkdir(parents=True, exist_ok=False)
                    exporter(destination)

                runtime_storage_root = temporary / "runtime-storage"
                for storage_key, exporter in sorted(
                    snapshot.runtime_storage_exporters.items()
                ):
                    if not _SAFE_AGENT_KEY.fullmatch(storage_key):
                        raise ValueError(f"unsafe runtime storage key: {storage_key!r}")
                    destination = runtime_storage_root / storage_key
                    destination.mkdir(parents=True, exist_ok=False)
                    exporter(destination)

                files = []
                for file_path in sorted(temporary.rglob("*")):
                    if file_path.is_symlink():
                        raise ValueError(
                            f"checkpoint exporter created a symlink: {file_path}"
                        )
                    if not file_path.is_file():
                        continue
                    self._fsync_file(file_path)
                    files.append(
                        {
                            "path": file_path.relative_to(temporary).as_posix(),
                            "size": file_path.stat().st_size,
                            "sha256": _sha256(file_path),
                        }
                    )
                bundle = {
                    "bundle_schema_version": self.BUNDLE_SCHEMA_VERSION,
                    "run_id": str(result.run_id),
                    "attempt_id": str(result.attempt_id),
                    "step_no": result.step_no,
                    "virtual_time": result.virtual_time.isoformat(),
                    "frame_sha256": frame.sha256,
                    "files": files,
                }
                bundle_path = temporary / "bundle.json"
                self._write_bytes(bundle_path, _canonical_json(bundle))
                bundle_sha256 = _sha256(bundle_path)
                self._fsync_directory_tree(temporary)
                os.rename(temporary, target)
                self._fsync_directory(target.parent)
                validated = self.validate(target)
                if validated.bundle_sha256 != bundle_sha256:
                    raise CheckpointConflictError(
                        "checkpoint changed during materialization"
                    )
                self._advance_latest(target.name, bundle_sha256)
                self._prune_old()
                return target
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    def validate(self, path: Path) -> StoredCheckpoint:
        """执行 `CheckpointBundleWriter` 的`validate`操作。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            返回计算得到的整数值或版本号。
        """
        with self.access():
            return self._validate_locked(path)

    def _validate_locked(self, path: Path) -> StoredCheckpoint:
        """校验`locked`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        resolved = path.resolve()
        checkpoint_root = self._paths.checkpoints.resolve()
        if resolved.parent != checkpoint_root:
            raise ValueError("checkpoint path is outside this run")
        bundle_path = resolved / "bundle.json"
        with bundle_path.open("r", encoding="utf-8") as file_handle:
            bundle = json.load(file_handle)
        if bundle.get("bundle_schema_version") != self.BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint bundle: {path}")
        if bundle.get("run_id") != str(self._paths.run_id):
            raise ValueError(f"checkpoint run_id mismatch: {path}")
        match = re.fullmatch(r"step-([0-9]{6})", resolved.name)
        if match is None or int(match.group(1)) != bundle.get("step_no"):
            raise ValueError(f"checkpoint directory and bundle step mismatch: {path}")
        declared_files = bundle.get("files", [])
        if not isinstance(declared_files, list):
            raise ValueError(f"checkpoint files manifest is invalid: {path}")
        declared_paths: set[str] = set()
        frame_item = None
        for item in declared_files:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe checkpoint member: {relative}")
            normalized = relative.as_posix()
            if normalized in declared_paths:
                raise ValueError(f"duplicate checkpoint member: {relative}")
            declared_paths.add(normalized)
            file_path = resolved / relative
            if not file_path.is_file() or file_path.is_symlink():
                raise ValueError(f"missing or unsafe checkpoint member: {relative}")
            if (
                file_path.stat().st_size != item["size"]
                or _sha256(file_path) != item["sha256"]
            ):
                raise ValueError(f"checkpoint member hash mismatch: {relative}")
            if normalized == "frame.json.gz":
                frame_item = item
        if not {"state.json", "conversation.json", "frame.json.gz"}.issubset(
            declared_paths
        ):
            raise ValueError(f"checkpoint is missing required members: {path}")
        actual_paths = {
            member.relative_to(resolved).as_posix()
            for member in resolved.rglob("*")
            if member.is_file() and member.name != "bundle.json"
        }
        if actual_paths != declared_paths:
            raise ValueError(f"checkpoint contains undeclared members: {path}")
        if frame_item is None or frame_item["sha256"] != bundle.get("frame_sha256"):
            raise ValueError(f"checkpoint frame hash does not match bundle: {path}")
        with gzip.open(resolved / "frame.json.gz", "rt", encoding="utf-8") as handle:
            frame_document = json.load(handle)
        frame_result = frame_document.get("result", {})
        if (
            frame_result.get("run_id") != bundle.get("run_id")
            or frame_result.get("attempt_id") != bundle.get("attempt_id")
            or frame_result.get("step_no") != bundle.get("step_no")
            or frame_result.get("virtual_time") != bundle.get("virtual_time")
        ):
            raise ValueError(f"checkpoint frame semantics do not match bundle: {path}")
        with (resolved / "state.json").open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        if "virtual_time" in state and state.get("virtual_time") != bundle.get(
            "virtual_time"
        ):
            raise ValueError(f"checkpoint state time does not match bundle: {path}")
        return StoredCheckpoint(
            path=resolved,
            bundle_sha256=_sha256(bundle_path),
            created=False,
        )

    def select_for_recovery(
        self,
        step_no: int,
        *,
        orphan_root: Path,
    ) -> StoredCheckpoint:
        """执行 `CheckpointBundleWriter` 的`select``for``recovery`操作。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            orphan_root: `orphan`使用的根目录路径。 类型：`Path`。

        返回:
            返回计算得到的整数值或版本号。
        """

        with self.access():
            return self._select_for_recovery_locked(step_no, orphan_root=orphan_root)

    def _select_for_recovery_locked(
        self,
        step_no: int,
        *,
        orphan_root: Path,
    ) -> StoredCheckpoint:
        """执行`select``for``recovery``locked`的内部处理，供当前模块或类复用。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            orphan_root: `orphan`使用的根目录路径。 类型：`Path`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if step_no < 1:
            raise ValueError("recovery checkpoint step must be positive")
        checkpoint_root = self._paths.checkpoints.resolve()
        target = checkpoint_root / f"step-{step_no:06d}"
        selected = self.validate(target)
        orphan_root = orphan_root.resolve()
        allowed_orphan_root = self._paths.orphaned.resolve()
        if not orphan_root.is_relative_to(allowed_orphan_root):
            raise ValueError("checkpoint orphan destination is outside this run")
        candidates = sorted(checkpoint_root.glob("step-[0-9][0-9][0-9][0-9][0-9][0-9]"))
        for candidate in candidates:
            match = re.fullmatch(r"step-([0-9]{6})", candidate.name)
            if match is None or int(match.group(1)) <= step_no:
                continue
            resolved_candidate = candidate.resolve()
            if resolved_candidate.parent != checkpoint_root or candidate.is_symlink():
                raise ValueError("unsafe future checkpoint path")
            orphan_root.mkdir(parents=True, exist_ok=True)
            destination = orphan_root / candidate.name
            if destination.exists():
                raise ValueError("future checkpoint was already orphaned")
            os.replace(resolved_candidate, destination)
        self._advance_latest(target.name, selected.bundle_sha256)
        return selected

    def read_latest(self) -> StoredCheckpoint | None:
        """读取`latest`。

        返回:
            返回计算得到的整数值或版本号。 没有可用结果时返回 `None`。
        """
        with self.access():
            return self._read_latest_locked()

    def _read_latest_locked(self) -> StoredCheckpoint | None:
        """读取`latest``locked`。

        返回:
            返回计算得到的整数值或版本号。 没有可用结果时返回 `None`。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        latest = self._paths.checkpoints / "LATEST"
        if latest.exists():
            try:
                with latest.open("r", encoding="utf-8") as file_handle:
                    pointer = json.load(file_handle)
                name = pointer.get("checkpoint")
                if not isinstance(name, str) or not re.fullmatch(
                    r"step-[0-9]{6}", name
                ):
                    raise ValueError("invalid checkpoint LATEST pointer")
                checkpoint = self.validate(self._paths.checkpoints / name)
                if checkpoint.bundle_sha256 != pointer.get("bundle_sha256"):
                    raise ValueError("checkpoint LATEST hash mismatch")
                return checkpoint
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

        # LATEST 指针只是查询优化。崩溃恢复时应选择最新且完整通过校验的不可变包，
        # 不能直接信任可能损坏的 LATEST。
        candidates = sorted(
            (
                path
                for path in self._paths.checkpoints.iterdir()
                if path.is_dir() and re.fullmatch(r"step-[0-9]{6}", path.name)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        for candidate in candidates:
            try:
                checkpoint = self.validate(candidate)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            self._advance_latest(candidate.name, checkpoint.bundle_sha256)
            return checkpoint
        return None

    def _prune_old(self) -> None:
        """执行`prune``old`的内部处理，供当前模块或类复用。

        返回:
            无返回值。
        """
        try:
            self._prune_old_locked()
        except OSError as exc:
            # 保留清理发生在新包与 LATEST 均持久化之后，只属于维护操作。
            # 索引器、防病毒软件或 ACL 问题不能把有效仿真步变成 WORKER_ERROR；后续写入会重试清理。
            self._defer_prune(self._paths.checkpoints, exc)

    def _prune_old_locked(self) -> None:
        # 旧进程可能已发布删除墓碑后退出，但文件仍被防病毒软件或索引器占用。
        # 优先重试这些私有目录；它们从不会作为可用检查点对外暴露。
        """执行`prune``old``locked`的内部处理，供当前模块或类复用。

        返回:
            无返回值。
        """
        for tombstone in list(self._paths.checkpoints.iterdir()):
            if tombstone.is_dir() and _PRUNE_TOMBSTONE.fullmatch(tombstone.name):
                self._delete_tombstone(tombstone)

        candidates = sorted(
            (
                path.resolve()
                for path in self._paths.checkpoints.iterdir()
                if path.is_dir() and re.fullmatch(r"step-[0-9]{6}", path.name)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        root = self._paths.checkpoints.resolve()
        for candidate in candidates[self._retention :]:
            if candidate.parent != root or candidate.is_symlink():
                continue
            tombstone = root / f".prune-{candidate.name}-{uuid4()}.tmp"
            try:
                # 绝不直接递归删除已发布检查点。先重命名可确保发生共享冲突时完整包仍在，
                # 不会重现只删除一半的损坏状态。
                os.replace(candidate, tombstone)
            except OSError as exc:
                self._defer_prune(candidate, exc)
                continue
            self._delete_tombstone(tombstone)

    def _delete_tombstone(self, tombstone: Path) -> None:
        """删除`tombstone`。

        参数:
            tombstone: 已从公开检查点命名空间移出的待清理私有目录。 类型：`Path`。

        返回:
            无返回值。
        """
        for delay in (0.0, 0.05, 0.15, 0.30):
            if delay:
                time.sleep(delay)
            try:
                shutil.rmtree(tombstone)
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
        self._defer_prune(tombstone, last_error)

    def _defer_prune(self, path: Path, exc: OSError) -> None:
        """执行`defer``prune`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            exc: 上游捕获的异常对象，用于分类、脱敏或转换错误信息。 类型：`OSError`。

        返回:
            无返回值。
        """
        _LOGGER.warning(
            "checkpoint retention deferred for run=%s path=%s error=%s",
            self._paths.run_id,
            path.name,
            type(exc).__name__,
        )

    def _advance_latest(self, checkpoint: str, bundle_sha256: str) -> None:
        """执行`advance``latest`的内部处理，供当前模块或类复用。

        参数:
            checkpoint: 当前运行已验证的检查点记录或快照。 类型：`str`。
            bundle_sha256: `bundle`的内容摘要，用于完整性和幂等校验。 类型：`str`。

        返回:
            无返回值。
        """
        latest = self._paths.checkpoints / "LATEST"
        temporary = self._paths.checkpoints / f".LATEST-{uuid4()}.tmp"
        try:
            self._write_bytes(
                temporary,
                _canonical_json(
                    {"checkpoint": checkpoint, "bundle_sha256": bundle_sha256}
                ),
            )
            os.replace(temporary, latest)
            self._fsync_directory(latest.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        """写入`bytes`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            content: 待解析、写入、哈希或发送给下游组件的正文内容。 类型：`bytes`。

        返回:
            无返回值。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())

    @staticmethod
    def _fsync_file(path: Path) -> None:
        # Windows 要求使用可写文件描述符调用 fsync()。
        """执行`fsync``file`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            无返回值。
        """
        with path.open("r+b") as file_handle:
            os.fsync(file_handle.fileno())

    @classmethod
    def _fsync_directory_tree(cls, root: Path) -> None:
        """执行`fsync``directory``tree`的内部处理，供当前模块或类复用。

        参数:
            root: 受控存储区域的根目录；派生路径不得逃逸该目录。 类型：`Path`。

        返回:
            无返回值。
        """
        if os.name == "nt":
            return
        for directory in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            cls._fsync_directory(directory)
        cls._fsync_directory(root)

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
