"""Canonical JSON and revision hashing."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


def _normalize(value: Any) -> Any:
    """执行`normalize`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`Any`。

    返回:
        返回 `Any` 类型的处理结果。
    """
    if isinstance(value, str):
        return unicodedata.normalize(
            "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
        )
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return value


def canonical_json_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    """执行 的`canonical``json``bytes`操作。

    参数:
        value: 当前操作使用的`value`。 类型：`BaseModel | Mapping[str, Any]`。

    返回:
        返回 `bytes` 类型的处理结果。
    """

    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json", exclude_none=False)
    else:
        raw = dict(value)
    normalized = _normalize(raw)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def definition_hash(value: BaseModel | Mapping[str, Any]) -> str:
    """执行 的仿真定义哈希值操作。

    参数:
        value: 当前操作使用的`value`。 类型：`BaseModel | Mapping[str, Any]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def content_hash(content: str) -> str:
    """执行 的`content`哈希值操作。

    参数:
        content: 待解析、写入、哈希或发送给下游组件的正文内容。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    normalized = _normalize(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
