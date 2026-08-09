"""Append-only physical and logical model-call facts for one run attempt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from .context import RunPaths


class ModelTraceEventType(StrEnum):
    PHYSICAL_ATTEMPT = "PHYSICAL_ATTEMPT"
    LOGICAL_END = "LOGICAL_END"


class ModelTraceStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FALLBACK = "FALLBACK"


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact_text(value: str | None, *, maximum_length: int | None = None) -> str | None:
    """Redact credential-shaped text without implicitly truncating stored facts."""

    if value is None:
        return None
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted if maximum_length is None else redacted[:maximum_length]


def redact_error(value: str | None) -> str | None:
    """Return a bounded, redacted diagnostic suitable for DB/UI error fields."""

    return redact_text(value, maximum_length=2000)


@dataclass(frozen=True, slots=True)
class ModelTraceEvent:
    event_type: ModelTraceEventType
    run_id: UUID
    attempt_id: UUID
    call_id: UUID
    step_no: int | None
    agent_key: str | None
    purpose: str
    prompt_key: str | None
    provider: str
    resolved_model: str
    started_at: datetime
    ended_at: datetime
    latency_ms: int
    attempt_no: int | None
    status: ModelTraceStatus
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("trace timestamps must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must not be negative")
        if self.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT:
            if self.attempt_no is None or self.attempt_no < 1:
                raise ValueError("physical attempts require a positive attempt_no")


class ModelTraceWriter:
    """A process-exclusive JSONL writer with strictly increasing event_seq."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        paths: RunPaths,
        *,
        run_id: UUID,
        attempt_id: UUID,
        attempt_no: int,
        capture_payloads: bool,
    ):
        if paths.run_id != run_id:
            raise ValueError("trace writer run_id does not own these paths")
        if attempt_no < 1:
            raise ValueError("attempt_no must be positive")
        paths.ensure()
        self.path = paths.traces / f"model-calls-{attempt_no:03d}.jsonl"
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._capture_payloads = capture_payloads
        self._lock = threading.Lock()
        self._event_seq = self._read_last_sequence()

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def attempt_id(self) -> UUID:
        return self._attempt_id

    def _read_last_sequence(self) -> int:
        if not self.path.exists():
            return 0
        content = self.path.read_bytes()
        if content and not content.endswith(b"\n"):
            raise ValueError("model trace ends with an incomplete JSONL record")
        last = 0
        for raw_line in content.splitlines():
            record = json.loads(raw_line)
            if record.get("run_id") != str(self._run_id):
                raise ValueError("model trace run_id mismatch")
            if record.get("attempt_id") != str(self._attempt_id):
                raise ValueError("model trace attempt_id mismatch")
            sequence = record.get("event_seq")
            if sequence != last + 1:
                raise ValueError("model trace event_seq is not contiguous")
            last = sequence
        return last

    def append(self, event: ModelTraceEvent) -> int:
        if event.run_id != self._run_id or event.attempt_id != self._attempt_id:
            raise ValueError("trace event belongs to another run or attempt")
        with self._lock:
            sequence = self._event_seq + 1
            record = self._to_record(event, sequence)
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            with self.path.open("ab") as file_handle:
                file_handle.write(encoded)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            self._event_seq = sequence
            return sequence

    def _to_record(self, event: ModelTraceEvent, sequence: int) -> dict[str, Any]:
        record = asdict(event)
        for key in ("event_type", "status"):
            record[key] = record[key].value
        for key in ("run_id", "attempt_id", "call_id"):
            record[key] = str(record[key])
        for key in ("started_at", "ended_at"):
            record[key] = record[key].isoformat()
        record["schema_version"] = self.SCHEMA_VERSION
        record["event_seq"] = sequence
        record["error_summary"] = redact_error(record.get("error_summary"))
        payload = record.pop("payload", None)
        if self._capture_payloads and payload is not None:
            payload_bytes = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            record["payload"] = payload
            record["payload_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
        return record
