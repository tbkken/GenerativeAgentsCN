"""Streamed, size-limited, content-addressed asset materialization."""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from filelock import FileLock


class AssetValidationError(ValueError):
    """上传资产的大小、类型、哈希或目标路径不满足安全约束。"""

    pass


_EXTENSIONS = {
    "application/json": ".json",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


@dataclass(frozen=True, slots=True)
class AssetBlob:
    """已经落盘并按内容哈希寻址的不可变资产元数据。"""

    sha256: str
    logical_name: str
    media_type: str
    size_bytes: int
    relative_path: str
    absolute_path: Path
    created: bool


class AssetStore:
    """校验上传内容，并把相同字节去重保存到受控资源目录。

    所有目标路径都由服务端根据摘要生成；调用方提供的文件名只用于展示，不能参与
    磁盘路径解析，从而阻止目录穿越和跨资源覆盖。
    """

    def __init__(self, var_dir: str | Path, *, max_bytes: int = 50 * 1024 * 1024):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。
            max_bytes: `bytes`允许的最大值。 类型：`int`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.root = Path(var_dir).resolve() / "assets"
        self.content_root = self.root / "sha256"
        self.temporary_root = self.root / ".tmp"
        self.lock_root = self.root / ".locks"
        self.max_bytes = max_bytes
        for path in (self.content_root, self.temporary_root, self.lock_root):
            path.mkdir(parents=True, exist_ok=True)

    def put_stream(
        self,
        stream: BinaryIO,
        *,
        logical_name: str,
        declared_media_type: str | None = None,
    ) -> AssetBlob:
        """执行 `AssetStore` 的`put``stream`操作。

        参数:
            stream: 包含待导入资源内容的二进制输入流。 类型：`BinaryIO`。
            logical_name: 产物或资源在业务层使用的稳定逻辑名称。 类型：`str`。
            declared_media_type: 调用方声明的媒体类型；为空时根据内容进行受控推断。 类型：`str | None`。 默认值：`None`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            AssetValidationError: 当底层操作报告该异常条件时抛出。
        """
        safe_name = self._safe_logical_name(logical_name)
        temporary = self.temporary_root / f"upload-{uuid4()}.tmp"
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with temporary.open("xb") as target:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    if not isinstance(block, bytes):
                        raise AssetValidationError("asset stream must return bytes")
                    size += len(block)
                    if size > self.max_bytes:
                        raise AssetValidationError(
                            f"asset exceeds maximum size of {self.max_bytes} bytes"
                        )
                    if len(prefix) < 16:
                        prefix.extend(block[: 16 - len(prefix)])
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size == 0:
                raise AssetValidationError("asset must not be empty")
            media_type = self._detect_media_type(temporary, bytes(prefix))
            normalized_declared = (
                declared_media_type.split(";", 1)[0].strip().lower()
                if declared_media_type
                else None
            )
            if normalized_declared and normalized_declared != media_type:
                raise AssetValidationError(
                    f"declared media type {normalized_declared!r} does not match {media_type!r}"
                )
            sha256 = digest.hexdigest()
            extension = _EXTENSIONS[media_type]
            directory = self.content_root / sha256[:2] / sha256
            target = directory / f"content{extension}"
            with FileLock(str(self.lock_root / f"{sha256}.lock"), timeout=10):
                if target.exists():
                    self._verify_file(target, sha256, size)
                    created = False
                else:
                    directory.mkdir(parents=True, exist_ok=True)
                    os.replace(temporary, target)
                    created = True
            return AssetBlob(
                sha256=sha256,
                logical_name=safe_name,
                media_type=media_type,
                size_bytes=size,
                relative_path=target.relative_to(self.root).as_posix(),
                absolute_path=target,
                created=created,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def resolve(
        self, relative_path: str, *, expected_sha256: str | None = None
    ) -> Path:
        """执行 `AssetStore` 的`resolve`操作。

        参数:
            relative_path: `relative`对应的文件系统路径。 类型：`str`。
            expected_sha256: 调用方或清单声明的 SHA-256，用于验证读取内容的完整性。 类型：`str | None`。 默认值：`None`。

        返回:
            返回目标文件或目录路径。

        异常:
            AssetValidationError: 当底层操作报告该异常条件时抛出。
            FileNotFoundError: 当所需文件或目录不存在时抛出。
        """
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise AssetValidationError("asset path must be a controlled relative path")
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.content_root.resolve()):
            raise AssetValidationError("asset path escapes content-addressed storage")
        if not resolved.is_file() or resolved.is_symlink():
            raise FileNotFoundError(relative_path)
        if expected_sha256 is not None:
            self._verify_file(resolved, expected_sha256, resolved.stat().st_size)
        return resolved

    @staticmethod
    def _safe_logical_name(value: str) -> str:
        """执行`safe``logical``name`的内部处理，供当前模块或类复用。

        参数:
            value: 当前操作使用的`value`。 类型：`str`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            AssetValidationError: 当底层操作报告该异常条件时抛出。
        """
        normalized = unicodedata.normalize("NFC", value).replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1].strip()
        if not name or name in {".", ".."} or any(ord(char) < 32 for char in name):
            raise AssetValidationError("logical asset name is invalid")
        return name[:255]

    @staticmethod
    def _detect_media_type(path: Path, prefix: bytes) -> str:
        """执行`detect``media``type`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            prefix: 生成稳定键、日志名或路径名时使用的前缀。 类型：`bytes`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            AssetValidationError: 当底层操作报告该异常条件时抛出。
        """
        if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if prefix.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            return "image/webp"
        try:
            with path.open("r", encoding="utf-8") as file_handle:
                json.load(file_handle)
            return "application/json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AssetValidationError(
                "unsupported or malformed asset content"
            ) from None

    @staticmethod
    def _verify_file(path: Path, expected_sha256: str, expected_size: int) -> None:
        """验证`file`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            expected_sha256: 调用方或清单声明的 SHA-256，用于验证读取内容的完整性。 类型：`str`。
            expected_size: `expected`的数量或容量。 类型：`int`。

        返回:
            无返回值。

        异常:
            AssetValidationError: 当底层操作报告该异常条件时抛出。
        """
        if path.stat().st_size != expected_size:
            raise AssetValidationError(
                "existing asset size does not match its content key"
            )
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            for block in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise AssetValidationError(
                "existing asset hash does not match its content key"
            )
