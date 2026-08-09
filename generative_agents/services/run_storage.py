"""Run-owned filesystem boundaries shared by observability services."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from io import BufferedReader
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterator

from generative_agents.persistence.models import Run

from .errors import ServiceError


class RunStorageBoundary:
    """Resolve database-owned paths without accepting client paths."""

    def __init__(self, var_dir: str | Path):
        self.var_dir = Path(var_dir).resolve()

    @staticmethod
    def _integrity_error() -> ServiceError:
        return ServiceError(
            "RUN_STORAGE_INTEGRITY_ERROR",
            "运行存储归属或完整性校验失败",
            status_code=500,
        )

    def run_root(self, run: Run) -> Path:
        expected = self.var_dir / "runs" / run.id
        relative = self._safe_relative(run.run_dir)
        configured = self.var_dir.joinpath(*relative.parts)
        if configured != expected:
            raise self._integrity_error()
        self._reject_reparse_chain(configured)
        resolved = configured.resolve()
        if not resolved.is_relative_to(self.var_dir) or resolved != expected.resolve():
            raise self._integrity_error()
        return resolved

    def owned_file(self, run: Run, relative_path: str, *, area: str) -> Path:
        if area not in {"logs", "traces", "artifacts", "frames"}:
            raise ValueError(f"unsupported run storage area: {area}")
        run_root = self.run_root(run)
        relative = self._safe_relative(relative_path)
        raw_path = self.var_dir.joinpath(*relative.parts)
        allowed_root = run_root / area
        self._reject_reparse_chain(raw_path)
        resolved = raw_path.resolve()
        if not resolved.is_relative_to(allowed_root.resolve()):
            raise self._integrity_error()
        return resolved

    @contextmanager
    def open_owned_binary(
        self,
        run: Run,
        relative_path: str,
        *,
        area: str,
    ) -> Iterator[tuple[Path, BufferedReader, os.stat_result]]:
        """Open an owned file and bind validation to the opened object.

        Path validation alone has a validate/open race.  The final descriptor
        target and identity are therefore checked after opening, and callers
        consume that same descriptor for hashing and parsing.
        """

        path = self.owned_file(run, relative_path, area=area)
        handle = path.open("rb")
        try:
            self._reject_reparse_chain(path)
            opened_stat = os.fstat(handle.fileno())
            current_stat = path.stat()
            if not stat.S_ISREG(opened_stat.st_mode) or not self._same_file(
                opened_stat, current_stat
            ):
                raise self._integrity_error()
            opened_path = self._opened_path(handle)
            if opened_path is not None:
                allowed_root = self.run_root(run) / area
                if not self._path_is_within(opened_path, allowed_root):
                    raise self._integrity_error()
            yield path, handle, opened_stat
        finally:
            handle.close()

    @classmethod
    def _safe_relative(cls, value: str) -> PurePosixPath:
        if not value or "\\" in value:
            raise cls._integrity_error()
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or ".." in posix.parts
            or "." in posix.parts
            or any(part in {"", "/"} for part in posix.parts)
        ):
            raise cls._integrity_error()
        return posix

    @staticmethod
    def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    @staticmethod
    def _path_is_within(path: Path, root: Path) -> bool:
        path_value = os.path.normcase(os.path.abspath(os.fspath(path)))
        root_value = os.path.normcase(os.path.abspath(os.fspath(root)))
        try:
            return os.path.commonpath([path_value, root_value]) == root_value
        except ValueError:
            return False

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            return bool(attributes & reparse_flag)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RunStorageBoundary._integrity_error() from exc

    @staticmethod
    def _opened_path(handle: BufferedReader) -> Path | None:
        if os.name == "nt":
            try:
                import ctypes
                import msvcrt

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                get_final_path = kernel32.GetFinalPathNameByHandleW
                get_final_path.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_wchar_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                ]
                get_final_path.restype = ctypes.c_uint32
                native_handle = msvcrt.get_osfhandle(handle.fileno())
                required = get_final_path(native_handle, None, 0, 0)
                if not required:
                    raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW")
                buffer = ctypes.create_unicode_buffer(required + 1)
                written = get_final_path(native_handle, buffer, len(buffer), 0)
                if not written or written >= len(buffer):
                    raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW")
                value = buffer.value
                if value.startswith("\\\\?\\UNC\\"):
                    value = "\\\\" + value[8:]
                elif value.startswith("\\\\?\\"):
                    value = value[4:]
                return Path(value)
            except (ImportError, OSError) as exc:
                raise RunStorageBoundary._integrity_error() from exc
        descriptor_path = Path(f"/proc/self/fd/{handle.fileno()}")
        if descriptor_path.exists():
            try:
                return descriptor_path.resolve(strict=True)
            except OSError as exc:
                raise RunStorageBoundary._integrity_error() from exc
        return None

    def _reject_reparse_chain(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.var_dir)
        except ValueError as exc:
            raise self._integrity_error() from exc
        current = self.var_dir
        if self._is_reparse_point(current):
            raise self._integrity_error()
        for part in relative.parts:
            current = current / part
            if self._is_reparse_point(current):
                raise self._integrity_error()
