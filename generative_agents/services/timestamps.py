"""Canonical serialization for wall-clock system timestamps."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_utc(value: datetime) -> str:
    """Serialize a persisted instant as timezone-aware UTC ISO-8601.

    SQLite returns timezone-naive values even for ``DateTime(timezone=True)``.
    All wall-clock columns in this service are stored as UTC, so restoring UTC
    here is part of the API contract rather than a browser-side guess.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["iso_utc"]
