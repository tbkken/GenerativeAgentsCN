"""Immutable checkpoint bundle writer and validator."""

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
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
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
    runtime_storage_exporters: Mapping[str, StorageExporter] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredCheckpoint:
    path: Path
    bundle_sha256: str
    created: bool


class CheckpointConflictError(RuntimeError):
    pass


class CheckpointBundleWriter:
    """Write a checkpoint as a verified directory and atomically advance LATEST."""

    BUNDLE_SCHEMA_VERSION = 1

    def __init__(
        self,
        paths: RunPaths,
        snapshot_provider: Callable[[StepResult], CheckpointSnapshot],
        *,
        retention: int = 2,
    ):
        if retention < 2:
            raise ValueError("checkpoint retention must be at least two")
        self._paths = paths
        self._snapshot_provider = snapshot_provider
        self._retention = retention
        self._paths.ensure()
        # Correctness is preferable to timing out a durable Step while a large
        # checkpoint preview/export is still consuming the previous bundle.
        self._checkpoint_lock = FileLock(str(self._paths.checkpoint_lock), timeout=-1)

    @contextmanager
    def access(self):
        """Serialize readers, publishers and retention across processes.

        Callers that need to validate and then consume several checkpoint files
        must keep this context open for the complete operation.  The lock is
        re-entrant for this writer, so calling ``validate`` inside it is safe.
        """

        with self._checkpoint_lock:
            yield

    def write(self, result: StepResult, frame: StoredFrame) -> Path:
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
                self._write_bytes(temporary / "state.json", _canonical_json(snapshot.state))
                self._write_bytes(
                    temporary / "conversation.json",
                    _canonical_json(snapshot.conversation),
                )
                shutil.copyfile(frame.path, temporary / "frame.json.gz")
                self._fsync_file(temporary / "frame.json.gz")

                storage_root = temporary / "storage"
                for agent_key, exporter in sorted(snapshot.storage_exporters.items()):
                    if not _SAFE_AGENT_KEY.fullmatch(agent_key):
                        raise ValueError(f"unsafe agent_key for storage path: {agent_key!r}")
                    destination = storage_root / agent_key / "associate"
                    destination.mkdir(parents=True, exist_ok=False)
                    exporter(destination)

                runtime_storage_root = temporary / "runtime-storage"
                for storage_key, exporter in sorted(
                    snapshot.runtime_storage_exporters.items()
                ):
                    if not _SAFE_AGENT_KEY.fullmatch(storage_key):
                        raise ValueError(
                            f"unsafe runtime storage key: {storage_key!r}"
                        )
                    destination = runtime_storage_root / storage_key
                    destination.mkdir(parents=True, exist_ok=False)
                    exporter(destination)

                files = []
                for file_path in sorted(temporary.rglob("*")):
                    if file_path.is_symlink():
                        raise ValueError(f"checkpoint exporter created a symlink: {file_path}")
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
                    raise CheckpointConflictError("checkpoint changed during materialization")
                self._advance_latest(target.name, bundle_sha256)
                self._prune_old()
                return target
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    def validate(self, path: Path) -> StoredCheckpoint:
        with self.access():
            return self._validate_locked(path)

    def _validate_locked(self, path: Path) -> StoredCheckpoint:
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
            if file_path.stat().st_size != item["size"] or _sha256(file_path) != item["sha256"]:
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
        if (
            "virtual_time" in state
            and state.get("virtual_time") != bundle.get("virtual_time")
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
        """Select the DB-authorized boundary and quarantine newer bundles."""

        with self.access():
            return self._select_for_recovery_locked(step_no, orphan_root=orphan_root)

    def _select_for_recovery_locked(
        self,
        step_no: int,
        *,
        orphan_root: Path,
    ) -> StoredCheckpoint:
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
        with self.access():
            return self._read_latest_locked()

    def _read_latest_locked(self) -> StoredCheckpoint | None:
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

        # The pointer is only an optimization. After a crash, recover the newest
        # fully verified immutable bundle rather than trusting a damaged LATEST.
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
        try:
            self._prune_old_locked()
        except OSError as exc:
            # Retention is maintenance after the new bundle and LATEST are
            # durable.  An indexer/antivirus/ACL problem must not turn a valid
            # simulation Step into WORKER_ERROR; a later write retries it.
            self._defer_prune(self._paths.checkpoints, exc)

    def _prune_old_locked(self) -> None:
        # A previous process may have published the tombstone and then exited
        # while an antivirus/indexer still held one of its files.  Retry those
        # private directories first; they are never visible as checkpoints.
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
                # Never recursively delete a published bundle.  Renaming first
                # means a sharing violation leaves the complete bundle intact,
                # rather than the half-deleted state from the original incident.
                os.replace(candidate, tombstone)
            except OSError as exc:
                self._defer_prune(candidate, exc)
                continue
            self._delete_tombstone(tombstone)

    def _delete_tombstone(self, tombstone: Path) -> None:
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
        _LOGGER.warning(
            "checkpoint retention deferred for run=%s path=%s error=%s",
            self._paths.run_id,
            path.name,
            type(exc).__name__,
        )

    def _advance_latest(self, checkpoint: str, bundle_sha256: str) -> None:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())

    @staticmethod
    def _fsync_file(path: Path) -> None:
        # Windows requires a writable file descriptor for fsync().
        with path.open("r+b") as file_handle:
            os.fsync(file_handle.fileno())

    @classmethod
    def _fsync_directory_tree(cls, root: Path) -> None:
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
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
