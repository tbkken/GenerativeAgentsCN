"""Bounded UTF-8 byte windows for controlled text previews."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .errors import ServiceError


@dataclass(frozen=True, slots=True)
class Utf8Window:
    """按字节游标返回的 UTF-8 安全文本窗口及下一页位置。"""

    start_cursor: int
    next_cursor: int
    content: str
    size_bytes: int
    file_id: str
    eof: bool


def file_identity(path: Path) -> tuple[str, int, int]:
    """执行 的`file``identity`操作。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

    返回:
        返回按接口约定组织的结果集合。
    """
    file_stat = path.stat()
    return file_identity_from_stat(file_stat)


def file_identity_from_stat(file_stat: os.stat_result) -> tuple[str, int, int]:
    """执行 的`file``identity``from``stat`操作。

    参数:
        file_stat: 目标文件的 `stat` 元数据，用于构造稳定文件身份。 类型：`os.stat_result`。

    返回:
        返回按接口约定组织的结果集合。
    """
    identity = hashlib.sha256(
        # Device + inode remains stable while an open log is appended.  ctime
        # is deliberately excluded because Linux updates it on every append.
        f"{file_stat.st_dev}:{file_stat.st_ino}".encode("ascii")
    ).hexdigest()[:24]
    return identity, file_stat.st_size, file_stat.st_mtime_ns


def _is_continuation(value: int) -> bool:
    """判断是否`continuation`。

    参数:
        value: 当前操作使用的`value`。 类型：`int`。

    返回:
        条件成立时返回 `True`，否则返回 `False`。
    """
    return value & 0b1100_0000 == 0b1000_0000


def read_utf8_bytes(
    value: bytes,
    *,
    cursor: int,
    limit_bytes: int,
    file_id: str | None = None,
    tail: bool = False,
    encoding_code: str = "TEXT_ENCODING_INVALID",
) -> Utf8Window:
    """按字节窗口读取 UTF-8 文本，并保证不会返回残缺字符。

    参数:
        value: 当前操作使用的`value`。 类型：`bytes`。
        cursor: 分页游标；为空时从结果集起点开始读取。 类型：`int`。
        limit_bytes: 本次最多读取或返回的字节数；UTF-8 边界修正后可能略少。 类型：`int`。
        file_id: `file`的唯一标识。 类型：`str | None`。 默认值：`None`。
        tail: 是否从日志或轨迹文件末尾向前读取最新窗口。 类型：`bool`。 默认值：`False`。
        encoding_code: 文本编码探测或解码失败时返回的稳定诊断码。 类型：`str`。 默认值：`'TEXT_ENCODING_INVALID'`。

    返回:
        返回 `Utf8Window` 类型的处理结果。

    异常:
        ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。

    说明:
        起止偏移是字节位置而不是字符位置。函数会把边界收缩到合法 UTF-8 字符边界，既不使用替换字符，也不静默跳过损坏字节。
    """

    size = len(value)
    if cursor < 0 or cursor > size:
        raise ServiceError(
            "INVALID_BYTE_CURSOR", "字节游标超出内容范围", status_code=422
        )
    if limit_bytes < 1 or limit_bytes > 262_144:
        raise ServiceError(
            "INVALID_BYTE_LIMIT",
            "读取窗口必须在 1 到 262144 字节之间",
            status_code=422,
        )

    start = max(0, size - limit_bytes) if tail else cursor
    if tail:
        while start < size and _is_continuation(value[start]):
            start += 1
    elif start < size and _is_continuation(value[start]):
        raise ServiceError(
            "INVALID_BYTE_CURSOR",
            "字节游标必须位于 UTF-8 字符边界",
            status_code=422,
            details={"cursor": cursor},
        )

    raw = value[start : min(size, start + limit_bytes)]
    while raw:
        try:
            content = raw.decode("utf-8", errors="strict")
            break
        except UnicodeDecodeError as exc:
            if exc.end == len(raw) and exc.reason == "unexpected end of data":
                raw = raw[: exc.start]
                continue
            raise ServiceError(
                encoding_code,
                "内容不是有效的 UTF-8 文本",
                status_code=422,
                details={"cursor": start + exc.start},
            ) from exc
    else:
        content = ""

    next_cursor = start + len(raw)
    if next_cursor == start and start < size:
        raise ServiceError(
            "INVALID_BYTE_LIMIT",
            "读取窗口不足以容纳下一个 UTF-8 字符",
            status_code=422,
            details={"cursor": start},
        )
    return Utf8Window(
        start_cursor=start,
        next_cursor=next_cursor,
        content=content,
        size_bytes=size,
        file_id=file_id or hashlib.sha256(value).hexdigest()[:24],
        eof=next_cursor >= size,
    )


def read_utf8_window(
    path: Path,
    *,
    cursor: int,
    limit_bytes: int,
    tail: bool = False,
    expected_file_id: str | None = None,
    missing_code: str = "TEXT_CONTENT_MISSING",
    truncated_code: str = "TEXT_CONTENT_TRUNCATED",
    rotated_code: str = "TEXT_CONTENT_ROTATED",
    encoding_code: str = "TEXT_ENCODING_INVALID",
) -> Utf8Window:
    """读取`utf8``window`。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
        cursor: 分页游标；为空时从结果集起点开始读取。 类型：`int`。
        limit_bytes: 本次最多读取或返回的字节数；UTF-8 边界修正后可能略少。 类型：`int`。
        tail: 是否从日志或轨迹文件末尾向前读取最新窗口。 类型：`bool`。 默认值：`False`。
        expected_file_id: `expected``file`的唯一标识。 类型：`str | None`。 默认值：`None`。
        missing_code: 传入当前算法的`missing``code`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。 默认值：`'TEXT_CONTENT_MISSING'`。
        truncated_code: 内容被截断时返回给调用方的稳定错误码。 类型：`str`。 默认值：`'TEXT_CONTENT_TRUNCATED'`。
        rotated_code: 传入当前算法的`rotated``code`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。 默认值：`'TEXT_CONTENT_ROTATED'`。
        encoding_code: 文本编码探测或解码失败时返回的稳定诊断码。 类型：`str`。 默认值：`'TEXT_ENCODING_INVALID'`。

    返回:
        返回 `Utf8Window` 类型的处理结果。

    异常:
        ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
    """
    if cursor < 0:
        raise ServiceError("INVALID_BYTE_CURSOR", "字节游标不能为负数", status_code=422)
    if limit_bytes < 1 or limit_bytes > 262_144:
        raise ServiceError(
            "INVALID_BYTE_LIMIT",
            "读取窗口必须在 1 到 262144 字节之间",
            status_code=422,
        )
    if not path.is_file() or path.is_symlink():
        raise ServiceError(missing_code, "受控文本文件不存在", status_code=410)
    identity, size, _mtime_ns = file_identity(path)
    if expected_file_id and expected_file_id != identity:
        raise ServiceError(
            rotated_code,
            "文件已轮转，请从新文件重新读取",
            status_code=409,
            details={"reset_cursor": 0, "file_id": identity, "size_bytes": size},
        )
    if cursor > size:
        raise ServiceError(
            truncated_code,
            "文件已截断，请重置读取游标",
            status_code=409,
            details={"reset_cursor": 0, "file_id": identity, "size_bytes": size},
        )
    start = max(0, size - limit_bytes) if tail else cursor
    with path.open("rb") as handle:
        if tail:
            # Probe at most three bytes before the requested suffix so a start
            # inside a multibyte code point can move back to its lead byte.
            probe_start = max(0, start - 3)
            handle.seek(probe_start)
            raw = handle.read(min(size - probe_start, limit_bytes + 3))
            offset = start - probe_start
            while offset > 0 and offset < len(raw) and _is_continuation(raw[offset]):
                offset -= 1
            start = probe_start + offset
            raw = raw[offset:]
        elif start < size:
            handle.seek(start)
            marker = handle.read(1)
            if marker and _is_continuation(marker[0]):
                raise ServiceError(
                    "INVALID_BYTE_CURSOR",
                    "字节游标必须位于 UTF-8 字符边界",
                    status_code=422,
                    details={"cursor": cursor},
                )
        if not tail:
            handle.seek(start)
            # UTF-8 code points are at most four bytes.  The three-byte
            # allowance completes the code point crossed by the target limit.
            raw = handle.read(limit_bytes + 3)

    target_length = min(limit_bytes, len(raw), size - start)
    consumed = len(raw) if tail else target_length
    while (
        not tail
        and consumed < len(raw)
        and consumed < size - start
        and _is_continuation(raw[consumed])
    ):
        consumed += 1
    selected = raw[:consumed]
    try:
        content = selected.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ServiceError(
            encoding_code,
            "内容不是有效的 UTF-8 文本",
            status_code=422,
            details={"cursor": start + exc.start},
        ) from exc
    next_cursor = start + len(selected)
    return Utf8Window(
        start_cursor=start,
        next_cursor=next_cursor,
        content=content,
        size_bytes=size,
        file_id=identity,
        eof=next_cursor >= size,
    )


def read_utf8_handle(
    handle: BinaryIO,
    *,
    cursor: int,
    limit_bytes: int,
    file_stat: os.stat_result | None = None,
    truncated_code: str = "TEXT_CONTENT_TRUNCATED",
    encoding_code: str = "TEXT_ENCODING_INVALID",
) -> Utf8Window:
    """从已打开文件的字节窗口读取完整 UTF-8 字符。

    参数:
        handle: 已经打开并由调用方负责生命周期的二进制文件句柄。 类型：`BinaryIO`。
        cursor: 分页游标；为空时从结果集起点开始读取。 类型：`int`。
        limit_bytes: 本次最多读取或返回的字节数；UTF-8 边界修正后可能略少。 类型：`int`。
        file_stat: 目标文件的 `stat` 元数据，用于构造稳定文件身份。 类型：`os.stat_result | None`。 默认值：`None`。
        truncated_code: 内容被截断时返回给调用方的稳定错误码。 类型：`str`。 默认值：`'TEXT_CONTENT_TRUNCATED'`。
        encoding_code: 文本编码探测或解码失败时返回的稳定诊断码。 类型：`str`。 默认值：`'TEXT_ENCODING_INVALID'`。

    返回:
        返回 `Utf8Window` 类型的处理结果。

    异常:
        ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。

    说明:
        调用方必须传入二进制文件对象；窗口边界按 UTF-8 编码修正，返回的偏移仍以字节计。
    """

    if cursor < 0:
        raise ServiceError("INVALID_BYTE_CURSOR", "字节游标不能为负数", status_code=422)
    if limit_bytes < 1 or limit_bytes > 262_144:
        raise ServiceError(
            "INVALID_BYTE_LIMIT",
            "读取窗口必须在 1 到 262144 字节之间",
            status_code=422,
        )
    opened_stat = file_stat or os.fstat(handle.fileno())
    identity, size, _mtime_ns = file_identity_from_stat(opened_stat)
    if cursor > size:
        raise ServiceError(
            truncated_code,
            "文件已截断，请重置读取游标",
            status_code=409,
            details={"reset_cursor": 0, "file_id": identity, "size_bytes": size},
        )
    if cursor < size:
        handle.seek(cursor)
        marker = handle.read(1)
        if marker and _is_continuation(marker[0]):
            raise ServiceError(
                "INVALID_BYTE_CURSOR",
                "字节游标必须位于 UTF-8 字符边界",
                status_code=422,
                details={"cursor": cursor},
            )
    handle.seek(cursor)
    raw = handle.read(limit_bytes + 3)
    target_length = min(limit_bytes, len(raw), size - cursor)
    consumed = target_length
    while (
        consumed < len(raw)
        and consumed < size - cursor
        and _is_continuation(raw[consumed])
    ):
        consumed += 1
    selected = raw[:consumed]
    try:
        content = selected.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ServiceError(
            encoding_code,
            "内容不是有效的 UTF-8 文本",
            status_code=422,
            details={"cursor": cursor + exc.start},
        ) from exc
    next_cursor = cursor + len(selected)
    return Utf8Window(
        start_cursor=cursor,
        next_cursor=next_cursor,
        content=content,
        size_bytes=size,
        file_id=identity,
        eof=next_cursor >= size,
    )
