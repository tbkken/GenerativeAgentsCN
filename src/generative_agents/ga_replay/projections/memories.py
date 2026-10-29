"""Shared metadata projection for committed memory lifecycle facts."""
from __future__ import annotations

from typing import Any, Mapping


def memory_metadata(raw: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    """Retain creation evidence when later lifecycle deltas omit it."""
    result = {
        key: raw[key] if raw.get(key) is not None else previous.get(key)
        for key in ('source_event_id', 'subject', 'predicate', 'object', 'expires_at')
    }
    for key in ('address', 'evidence_memory_ids'):
        # Access/expiry/invalidation deltas can carry the DTO's empty defaults.
        # These facts do not clear the original memory's immutable evidence.
        result[key] = list(raw.get(key) or previous.get(key) or [])
    return result
