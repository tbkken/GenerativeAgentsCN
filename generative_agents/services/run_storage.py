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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self.var_dir = Path(var_dir).resolve()

    @staticmethod
    def _integrity_error() -> ServiceError:
        """执行`integrity``error`的内部处理，供当前模块或类复用。

        返回:
            返回 `ServiceError` 类型的处理结果。
        """
        return ServiceError(
            "RUN_STORAGE_INTEGRITY_ERROR",
            "运行存储归属或完整性校验失败",
            status_code=500,
        )

    def run_root(self, run: Run) -> Path:
        """执行 `RunStorageBoundary` 的运行`root`操作。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。

        返回:
            返回目标文件或目录路径。
        """
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
        """执行 `RunStorageBoundary` 的`owned``file`操作。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            relative_path: `relative`对应的文件系统路径。 类型：`str`。
            area: 传入当前算法的`area`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。

        返回:
            返回目标文件或目录路径。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
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
        """打开运行目录内受控文件，并把路径校验绑定到已打开文件对象。

        参数:
            run: 当前读取、控制、投影或生成产物的仿真运行记录。 类型：`Run`。
            relative_path: `relative`对应的文件系统路径。 类型：`str`。
            area: 传入当前算法的`area`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。

        返回:
            返回目标文件或目录路径。

        说明:
            先校验路径归属，再打开文件并复核实际文件对象，防止符号链接或路径替换造成越界读取。
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
        """执行`safe``relative`的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回目标文件或目录路径。
        """
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
        """执行`same``file`的内部处理，供当前模块或类复用。

        参数:
            left: 传入当前算法的`left`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`os.stat_result`。
            right: 传入当前算法的`right`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`os.stat_result`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    @staticmethod
    def _path_is_within(path: Path, root: Path) -> bool:
        """执行路径`is``within`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            root: 受控存储区域的根目录；派生路径不得逃逸该目录。 类型：`Path`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        path_value = os.path.normcase(os.path.abspath(os.fspath(path)))
        root_value = os.path.normcase(os.path.abspath(os.fspath(root)))
        try:
            return os.path.commonpath([path_value, root_value]) == root_value
        except ValueError:
            return False

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        """判断是否`reparse``point`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
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
        """执行`opened`路径的内部处理，供当前模块或类复用。

        参数:
            handle: 已经打开并由调用方负责生命周期的二进制文件句柄。 类型：`BufferedReader`。

        返回:
            返回目标文件或目录路径。 没有可用结果时返回 `None`。

        异常:
            OSError: 当底层操作报告该异常条件时抛出。
        """
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
        """执行`reject``reparse``chain`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            无返回值。
        """
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
