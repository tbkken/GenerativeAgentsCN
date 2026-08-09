"""Cursor-based, idempotent projection of append-only model call JSONL facts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Run,
    RunAttempt,
    RunModelTraceCursor,
    RunModelUsage,
    RunResultSummary,
)

from .context import RunPaths


class ModelTraceProjectionError(RuntimeError):
    pass


class ModelTraceProjector:
    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._var_dir = Path(var_dir).resolve()

    def project(
        self,
        *,
        run_id: str,
        attempt_id: str,
        relative_path: str,
    ) -> int:
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        trace_path = (self._var_dir / relative_path).resolve()
        if not trace_path.is_relative_to(paths.traces.resolve()):
            raise ModelTraceProjectionError("model trace path is outside the run trace directory")
        with self._database.session_factory() as read_session:
            if read_session.get(Run, run_id) is None:
                raise ModelTraceProjectionError("run does not exist")
            attempt = read_session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.run_id != run_id:
                raise ModelTraceProjectionError("model trace attempt does not own this run")
            current = read_session.get(RunModelTraceCursor, (run_id, attempt_id))
            byte_offset = current.byte_offset if current else 0
            last_event_seq = current.last_event_seq if current else 0
        if not trace_path.is_file():
            # ModelTraceWriter creates JSONL lazily on the first append. A
            # failure before the first model call is a legitimate zero-trace
            # attempt. A previously projected file disappearing is not.
            if current is None:
                return 0
            raise FileNotFoundError(trace_path)

        records, next_offset = self._read_complete_records(trace_path, byte_offset)
        if not records:
            return last_event_seq
        expected_sequence = last_event_seq
        for record in records:
            if record.get("run_id") != run_id or record.get("attempt_id") != attempt_id:
                raise ModelTraceProjectionError("model trace record scope mismatch")
            if record.get("event_seq") != expected_sequence + 1:
                raise ModelTraceProjectionError("model trace event sequence is not contiguous")
            expected_sequence += 1

        with self._database.session_factory.begin() as session:
            cursor = session.get(RunModelTraceCursor, (run_id, attempt_id))
            if cursor is None:
                cursor = RunModelTraceCursor(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    relative_path=relative_path,
                    last_event_seq=0,
                    byte_offset=0,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(cursor)
                session.flush()
            if cursor.last_event_seq != last_event_seq or cursor.byte_offset != byte_offset:
                # Another projector won the race. Re-read from its boundary.
                raise ModelTraceProjectionError("model trace cursor changed concurrently")
            logical_calls = 0
            retries = 0
            for record in records:
                self._apply_record(session, run_id, record)
                if record.get("event_type") == "LOGICAL_END":
                    logical_calls += 1
                elif (
                    record.get("event_type") == "PHYSICAL_ATTEMPT"
                    and (record.get("attempt_no") or 1) > 1
                ):
                    retries += 1
            cursor.last_event_seq = expected_sequence
            cursor.byte_offset = next_offset
            cursor.updated_at = datetime.now(timezone.utc)
            summary = session.get(RunResultSummary, run_id)
            if summary is not None:
                summary.model_call_count += logical_calls
                summary.model_retry_count += retries
                summary.result_version += 1
                summary.updated_at = datetime.now(timezone.utc)
            return expected_sequence

    @staticmethod
    def _read_complete_records(path: Path, offset: int) -> tuple[list[dict], int]:
        records: list[dict] = []
        consumed = 0
        with path.open("rb") as file_handle:
            file_handle.seek(offset)
            for raw_line in file_handle:
                if not raw_line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ModelTraceProjectionError("invalid complete JSONL record") from exc
                records.append(record)
                consumed += len(raw_line)
        return records, offset + consumed

    @staticmethod
    def _apply_record(session, run_id: str, record: dict) -> None:
        key = (
            run_id,
            str(record.get("purpose") or "unknown"),
            str(record.get("provider") or "unknown"),
            str(record.get("resolved_model") or "unknown"),
        )
        usage = session.get(RunModelUsage, key)
        if usage is None:
            usage = RunModelUsage(
                run_id=key[0],
                purpose=key[1],
                provider=key[2],
                resolved_model=key[3],
                logical_call_count=0,
                successful_call_count=0,
                fallback_count=0,
                physical_attempt_count=0,
                retry_count=0,
                input_tokens=None,
                output_tokens=None,
                latency_buckets_json={
                    "lt_100": 0,
                    "lt_500": 0,
                    "lt_1000": 0,
                    "lt_5000": 0,
                    "gte_5000": 0,
                },
                max_latency_ms=0,
                updated_step=record.get("step_no"),
            )
            session.add(usage)
            session.flush()
        event_type = record.get("event_type")
        if event_type == "PHYSICAL_ATTEMPT":
            usage.physical_attempt_count += 1
            if (record.get("attempt_no") or 1) > 1:
                usage.retry_count += 1
            latency = int(record.get("latency_ms") or 0)
            buckets = dict(usage.latency_buckets_json)
            buckets[ModelTraceProjector._latency_bucket(latency)] += 1
            usage.latency_buckets_json = buckets
            usage.max_latency_ms = max(usage.max_latency_ms, latency)
            usage.input_tokens = ModelTraceProjector._add_nullable(
                usage.input_tokens, record.get("prompt_tokens")
            )
            usage.output_tokens = ModelTraceProjector._add_nullable(
                usage.output_tokens, record.get("completion_tokens")
            )
        elif event_type == "LOGICAL_END":
            usage.logical_call_count += 1
            if record.get("status") == "SUCCEEDED":
                usage.successful_call_count += 1
            elif record.get("status") == "FALLBACK":
                usage.fallback_count += 1
        else:
            raise ModelTraceProjectionError(f"unknown model trace event type: {event_type}")
        usage.updated_step = record.get("step_no") or usage.updated_step

    @staticmethod
    def _latency_bucket(latency_ms: int) -> str:
        if latency_ms < 100:
            return "lt_100"
        if latency_ms < 500:
            return "lt_500"
        if latency_ms < 1000:
            return "lt_1000"
        if latency_ms < 5000:
            return "lt_5000"
        return "gte_5000"

    @staticmethod
    def _add_nullable(current: int | None, increment: int | None) -> int | None:
        if increment is None:
            return current
        return (current or 0) + int(increment)
