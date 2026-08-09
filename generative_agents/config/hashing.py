"""Canonical JSON and revision hashing."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return value


def canonical_json_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    """Serialize semantic JSON deterministically for snapshots and hashes."""

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
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def content_hash(content: str) -> str:
    normalized = _normalize(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
