"""Run/attempt-owned logs and model trace queries."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ArtifactJob,
    Run,
    RunAttempt,
    RunModelTraceCursor,
)
from generative_agents.runtime.model_trace import redact_error, redact_text

from .byte_windows import file_identity, read_utf8_bytes, read_utf8_window
from .errors import ServiceError, not_found
from .run_storage import RunStorageBoundary


_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b", re.I)
_SENSITIVE_KEYS = frozenset(
    {"authorization", "api_key", "apikey", "secret", "secret_ref", "token", "password"}
)


class LogService:
    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._boundary = RunStorageBoundary(var_dir)

    def list_attempts(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            rows = list(
                session.scalars(
                    select(RunAttempt)
                    .where(RunAttempt.run_id == run_id)
                    .order_by(RunAttempt.attempt_no)
                )
            )
            current = run.current_attempt_id
            items = [self._attempt_metadata(run, row) for row in rows]
        default_id = current or (items[-1]["attempt_id"] if items else None)
        return {"run_id": run_id, "default_attempt_id": default_id, "items": items}

    def read_attempt_log(
        self,
        run_id: str,
        attempt_id: str,
        *,
        cursor: int = 0,
        limit_bytes: int = 65_536,
        tail: bool = False,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        run, attempt, path = self._attempt_target(run_id, attempt_id)
        window = read_utf8_window(
            path,
            cursor=cursor,
            limit_bytes=limit_bytes,
            tail=tail,
            expected_file_id=file_id,
            missing_code="ATTEMPT_LOG_MISSING",
            truncated_code="ATTEMPT_LOG_TRUNCATED",
            rotated_code="ATTEMPT_LOG_ROTATED",
            encoding_code="ATTEMPT_LOG_ENCODING_INVALID",
        )
        fragments = self._record_window(
            path, window, terminal=attempt.status == "ENDED", discard_leading=tail
        )
        return {
            "run_id": run.id,
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "cursor": window.start_cursor,
            "next_cursor": window.next_cursor,
            "content": window.content,
            "records": fragments["records"],
            "starts_mid_line": fragments["starts_mid_line"],
            "ends_mid_line": fragments["ends_mid_line"],
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "eof": window.eof,
            "terminal": attempt.status == "ENDED",
        }

    def attempt_log_content(self, run_id: str, attempt_id: str) -> tuple[RunAttempt, Path]:
        _run, attempt, path = self._attempt_target(run_id, attempt_id)
        if not path.is_file() or path.is_symlink():
            raise ServiceError(
                "ATTEMPT_LOG_MISSING", "Attempt 日志不存在", status_code=410
            )
        return attempt, path

    def read_artifact_log(
        self,
        run_id: str,
        job_id: str,
        *,
        cursor: int = 0,
        limit_bytes: int = 65_536,
        tail: bool = False,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        run, job, path = self._artifact_target(run_id, job_id)
        window = read_utf8_window(
            path,
            cursor=cursor,
            limit_bytes=limit_bytes,
            tail=tail,
            expected_file_id=file_id,
            missing_code="ARTIFACT_LOG_MISSING",
            truncated_code="ARTIFACT_LOG_TRUNCATED",
            rotated_code="ARTIFACT_LOG_ROTATED",
            encoding_code="ARTIFACT_LOG_ENCODING_INVALID",
        )
        terminal = job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}
        fragments = self._record_window(
            path, window, terminal=terminal, discard_leading=tail
        )
        return {
            "run_id": run.id,
            "job_id": job.id,
            "cursor": window.start_cursor,
            "next_cursor": window.next_cursor,
            "content": window.content,
            "records": fragments["records"],
            "starts_mid_line": fragments["starts_mid_line"],
            "ends_mid_line": fragments["ends_mid_line"],
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "eof": window.eof,
            "terminal": terminal,
        }

    def artifact_log_content(self, run_id: str, job_id: str) -> tuple[ArtifactJob, Path]:
        _run, job, path = self._artifact_target(run_id, job_id)
        if not path.is_file() or path.is_symlink():
            raise ServiceError(
                "ARTIFACT_LOG_MISSING", "制品任务日志不存在", status_code=410
            )
        return job, path

    def model_traces(
        self,
        run_id: str,
        attempt_id: str,
        *,
        cursor: int = 0,
        limit: int = 100,
        purpose: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise ServiceError("INVALID_LIMIT", "limit 必须在 1 到 200 之间", status_code=422)
        run, attempt, trace, path = self._trace_target(run_id, attempt_id)
        if trace is None:
            return {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "attempt_no": attempt.attempt_no,
                "items": [],
                "next_cursor": 0,
                "eof": True,
                "available": False,
            }
        if not path.is_file() or path.is_symlink():
            raise ServiceError("MODEL_TRACE_MISSING", "模型调用追踪不存在", status_code=410)
        size = path.stat().st_size
        if cursor < 0 or cursor > size:
            raise ServiceError(
                "MODEL_TRACE_TRUNCATED",
                "模型追踪已截断，请重置游标",
                status_code=409,
                details={"reset_cursor": 0, "size_bytes": size},
            )
        items: list[dict[str, Any]] = []
        scanned = 0
        maximum_scan = 2 * 1024 * 1024
        with path.open("rb") as handle:
            if cursor:
                handle.seek(cursor - 1)
                if handle.read(1) != b"\n":
                    raise ServiceError(
                        "INVALID_TRACE_CURSOR",
                        "模型追踪游标必须来自上一页响应",
                        status_code=422,
                    )
            handle.seek(cursor)
            while len(items) < limit and scanned < maximum_scan:
                raw = handle.readline(262_145)
                if not raw:
                    break
                scanned += len(raw)
                if len(raw) > 262_144 or not raw.endswith(b"\n"):
                    if handle.tell() < size:
                        raise ServiceError(
                            "MODEL_TRACE_RECORD_TOO_LARGE",
                            "单条模型追踪超过读取上限",
                            status_code=422,
                        )
                    # A writer may currently own a trailing half line. Do not
                    # consume it until the newline is durable.
                    handle.seek(-len(raw), 1)
                    break
                try:
                    record = json.loads(raw.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ServiceError(
                        "MODEL_TRACE_INVALID", "模型追踪记录无效", status_code=422
                    ) from exc
                if record.get("run_id") != run_id or record.get("attempt_id") != attempt_id:
                    raise ServiceError(
                        "MODEL_TRACE_OWNERSHIP_INVALID",
                        "模型追踪归属无效",
                        status_code=500,
                    )
                if purpose and record.get("purpose") != purpose:
                    continue
                if status and record.get("status") != status:
                    continue
                if event_type and record.get("event_type") != event_type:
                    continue
                payload_available = "payload" in record
                record.pop("payload", None)
                record["payload_available"] = payload_available
                record["retry"] = bool(
                    record.get("event_type") == "PHYSICAL_ATTEMPT"
                    and (record.get("attempt_no") or 0) > 1
                )
                record["trace_id"] = self._trace_id(
                    attempt_id, int(record.get("event_seq", 0))
                )
                items.append(record)
            next_cursor = handle.tell()
        eof = next_cursor >= size
        return {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_no": attempt.attempt_no,
            "items": items,
            "next_cursor": next_cursor,
            "eof": eof,
            "available": True,
        }

    def model_trace_payload(
        self,
        run_id: str,
        attempt_id: str,
        event_seq: int,
        *,
        cursor: int = 0,
        limit_bytes: int = 16_384,
    ) -> dict[str, Any]:
        if event_seq < 1:
            raise ServiceError("INVALID_TRACE_EVENT", "event_seq 必须为正数", status_code=422)
        _run, _attempt, trace, path = self._trace_target(run_id, attempt_id)
        if trace is None or not path.is_file() or path.is_symlink():
            raise ServiceError("MODEL_TRACE_MISSING", "模型调用追踪不存在", status_code=410)
        record = None
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                try:
                    candidate = json.loads(raw.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ServiceError(
                        "MODEL_TRACE_INVALID", "模型调用追踪记录无效", status_code=422
                    ) from exc
                if candidate.get("event_seq") == event_seq:
                    record = candidate
                    break
        if record is None:
            raise not_found("model_trace_event", str(event_seq))
        if record.get("run_id") != run_id or record.get("attempt_id") != attempt_id:
            raise ServiceError(
                "MODEL_TRACE_OWNERSHIP_INVALID", "模型追踪归属无效", status_code=500
            )
        if "payload" not in record:
            raise ServiceError(
                "MODEL_TRACE_PAYLOAD_UNAVAILABLE", "该调用没有保存 payload", status_code=404
            )
        encoded = json.dumps(
            self._redact(record["payload"]), ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        if limit_bytes > 65_536:
            raise ServiceError(
                "INVALID_TRACE_PREVIEW_WINDOW", "模型 payload 预览窗口无效", status_code=422
            )
        window = read_utf8_bytes(
            encoded,
            cursor=cursor,
            limit_bytes=limit_bytes,
            file_id=hashlib.sha256(encoded).hexdigest()[:24],
            encoding_code="TRACE_PAYLOAD_ENCODING_INVALID",
        )
        return {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "event_seq": event_seq,
            "cursor": window.start_cursor,
            "next_cursor": None if window.eof else window.next_cursor,
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "content": window.content,
            "eof": window.eof,
        }

    def trace_detail(
        self,
        run_id: str,
        trace_id: str,
        *,
        cursor: int = 0,
        limit_bytes: int = 16_384,
    ) -> dict[str, Any]:
        attempt_id, event_seq = self._decode_trace_id(trace_id)
        _run, _attempt, trace, path = self._trace_target(run_id, attempt_id)
        if trace is None or path is None or not path.is_file() or path.is_symlink():
            raise ServiceError("MODEL_TRACE_MISSING", "模型调用追踪不存在", status_code=410)
        record = None
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                try:
                    candidate = json.loads(raw.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ServiceError(
                        "MODEL_TRACE_INVALID", "模型调用追踪记录无效", status_code=422
                    ) from exc
                if candidate.get("event_seq") == event_seq:
                    record = candidate
                    break
        if record is None:
            raise not_found("model_trace", trace_id)
        if record.get("run_id") != run_id or record.get("attempt_id") != attempt_id:
            raise ServiceError(
                "MODEL_TRACE_OWNERSHIP_INVALID", "模型调用追踪归属无效", status_code=500
            )
        metadata = self._redact({key: value for key, value in record.items() if key != "payload"})
        if "payload" not in record:
            return {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "event_seq": event_seq,
                "trace_id": trace_id,
                "trace": metadata,
                "payload_available": False,
                "cursor": 0,
                "next_cursor": None,
                "size_bytes": 0,
                "file_id": None,
                "content": None,
                "eof": True,
            }
        detail = self.model_trace_payload(
            run_id,
            attempt_id,
            event_seq,
            cursor=cursor,
            limit_bytes=limit_bytes,
        )
        detail.update(
            {
                "trace_id": trace_id,
                "trace": metadata,
                "payload_available": True,
            }
        )
        return detail

    @staticmethod
    def _trace_id(attempt_id: str, event_seq: int) -> str:
        raw = f"{attempt_id}:{event_seq}".encode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_trace_id(trace_id: str) -> tuple[str, int]:
        try:
            raw = base64.urlsafe_b64decode(trace_id + "=" * (-len(trace_id) % 4))
            attempt_id, sequence = raw.decode("ascii").rsplit(":", 1)
            event_seq = int(sequence)
            if not attempt_id or event_seq < 1:
                raise ValueError
        except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
            raise ServiceError(
                "INVALID_MODEL_TRACE_ID", "模型调用标识无效", status_code=422
            ) from exc
        return attempt_id, event_seq

    def _attempt_target(self, run_id: str, attempt_id: str):
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            attempt = session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.run_id != run_id:
                raise not_found("attempt", attempt_id)
            path = self._boundary.owned_file(run, attempt.log_path, area="logs")
            session.expunge(run)
            session.expunge(attempt)
        return run, attempt, path

    def _artifact_target(self, run_id: str, job_id: str):
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            job = session.get(ArtifactJob, job_id)
            if job is None or job.run_id != run_id:
                raise not_found("artifact_job", job_id)
            if not job.log_path:
                raise ServiceError(
                    "ARTIFACT_LOG_UNAVAILABLE",
                    "制品任务尚未产生可读取日志",
                    status_code=409,
                )
            path = self._boundary.owned_file(run, job.log_path, area="logs")
            session.expunge(run)
            session.expunge(job)
        return run, job, path

    def _trace_target(self, run_id: str, attempt_id: str):
        with self._database.session_factory() as session:
            run = self._run(session, run_id)
            attempt = session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.run_id != run_id:
                raise not_found("attempt", attempt_id)
            trace = session.get(RunModelTraceCursor, (run_id, attempt_id))
            path = (
                self._boundary.owned_file(run, trace.relative_path, area="traces")
                if trace is not None
                else None
            )
            session.expunge(run)
            session.expunge(attempt)
            if trace is not None:
                session.expunge(trace)
        return run, attempt, trace, path

    def _attempt_metadata(self, run: Run, attempt: RunAttempt) -> dict[str, Any]:
        path = self._boundary.owned_file(run, attempt.log_path, area="logs")
        exists = path.is_file() and not path.is_symlink()
        identity = size = modified = None
        if exists:
            identity, size, modified = file_identity(path)
        return {
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "status": attempt.status,
            "slot_no": attempt.slot_no,
            "start_step": attempt.start_step,
            "end_step": attempt.end_step,
            "started_at": attempt.started_at.isoformat(),
            "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
            "stop_reason": attempt.stop_reason,
            "error_code": attempt.error_code,
            "error_message": redact_error(attempt.error_message),
            "log": {
                "available": exists,
                "size_bytes": size or 0,
                "file_id": identity,
                "modified_at_ns": modified,
                "terminal": attempt.status == "ENDED",
            },
        }

    @staticmethod
    def _records(content: str) -> list[dict[str, Any]]:
        records = []
        for line in content.splitlines():
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                match = _LEVEL.search(line)
                records.append(
                    {
                        "level": (
                            "WARNING"
                            if match and match.group(1).upper() == "WARN"
                            else match.group(1).upper()
                            if match
                            else "INFO"
                        ),
                        "message": redact_error(line),
                    }
                )
                continue
            if not isinstance(value, dict):
                records.append({"level": "INFO", "message": redact_error(str(value))})
                continue
            records.append(
                {
                    "timestamp": value.get("timestamp") or value.get("time") or value.get("created_at"),
                    "level": str(value.get("level") or value.get("levelname") or "INFO").upper(),
                    "message": redact_error(str(value.get("message") or value.get("event") or value)),
                    "event": value.get("event"),
                }
            )
        return records

    @classmethod
    def _record_window(
        cls, path: Path, window, *, terminal: bool, discard_leading: bool
    ) -> dict[str, Any]:
        starts_mid_line = False
        if window.start_cursor > 0:
            with path.open("rb") as handle:
                handle.seek(window.start_cursor - 1)
                starts_mid_line = handle.read(1) != b"\n"
        ends_mid_line = bool(window.content) and not window.content.endswith("\n") and not (
            window.eof and terminal
        )
        complete = window.content
        if starts_mid_line:
            newline = complete.find("\n")
            if discard_leading:
                complete = "" if newline < 0 else complete[newline + 1 :]
            elif newline >= 0 or (window.eof and terminal):
                prefix = cls._line_prefix(path, window.start_cursor)
                complete = prefix + complete
            else:
                complete = ""
        if ends_mid_line:
            newline = complete.rfind("\n")
            complete = "" if newline < 0 else complete[: newline + 1]
        return {
            "records": cls._records(complete),
            "starts_mid_line": starts_mid_line,
            "ends_mid_line": ends_mid_line,
        }

    @staticmethod
    def _line_prefix(path: Path, cursor: int, *, maximum: int = 1_048_576) -> str:
        """Read only the current logical line prefix, never the whole log."""

        start = cursor
        collected = b""
        found_boundary = start == 0
        with path.open("rb") as handle:
            while start > 0 and len(collected) < maximum:
                length = min(4096, start, maximum - len(collected))
                start -= length
                handle.seek(start)
                block = handle.read(length)
                newline = block.rfind(b"\n")
                if newline >= 0:
                    collected = block[newline + 1 :] + collected
                    found_boundary = True
                    break
                collected = block + collected
        if not found_boundary and start > 0:
            raise ServiceError(
                "LOG_RECORD_TOO_LARGE",
                "单条日志超过结构化记录上限，请使用原始内容或下载",
                status_code=422,
            )
        try:
            return collected.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ServiceError(
                "LOG_RECORD_ENCODING_INVALID", "日志记录不是有效 UTF-8", status_code=422
            ) from exc

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if key.casefold() in _SENSITIVE_KEYS else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, str):
            # Payload paging must be lossless.  Bounded diagnostics use
            # ``redact_error``; trace payloads use the same redaction rules
            # without silently discarding content after 2 KiB.
            return redact_text(value)
        return value

    @staticmethod
    def _run(session, run_id: str) -> Run:
        run = session.get(Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run
