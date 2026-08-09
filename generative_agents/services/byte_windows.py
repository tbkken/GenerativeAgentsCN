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
    start_cursor: int
    next_cursor: int
    content: str
    size_bytes: int
    file_id: str
    eof: bool


def file_identity(path: Path) -> tuple[str, int, int]:
    file_stat = path.stat()
    return file_identity_from_stat(file_stat)


def file_identity_from_stat(file_stat: os.stat_result) -> tuple[str, int, int]:
    identity = hashlib.sha256(
        # Device + inode remains stable while an open log is appended.  ctime
        # is deliberately excluded because Linux updates it on every append.
        f"{file_stat.st_dev}:{file_stat.st_ino}".encode("ascii")
    ).hexdigest()[:24]
    return identity, file_stat.st_size, file_stat.st_mtime_ns


def _is_continuation(value: int) -> bool:
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
    """Read a UTF-8 window without replacing or silently skipping bytes.

    A client cursor must already be a code-point boundary.  Tail mode is the
    only mode allowed to calculate a start cursor and advance past continuation
    bytes.  A page that cannot fit its next complete character is rejected, so
    every successful non-EOF page makes byte-cursor progress.
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
    if cursor < 0:
        raise ServiceError(
            "INVALID_BYTE_CURSOR", "字节游标不能为负数", status_code=422
        )
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
    """Read a bounded immutable-text window from an already verified handle."""

    if cursor < 0:
        raise ServiceError(
            "INVALID_BYTE_CURSOR", "字节游标不能为负数", status_code=422
        )
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
    while consumed < len(raw) and consumed < size - cursor and _is_continuation(raw[consumed]):
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
