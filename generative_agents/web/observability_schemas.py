"""OpenAPI response contracts for Run observability and checkpoints."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from generative_agents.config.schema import StrictModel


class LogMetadataResponse(StrictModel):
    available: bool
    size_bytes: int = 0
    file_id: str | None = None
    modified_at_ns: int | None = None
    terminal: bool = False


class AttemptResponse(StrictModel):
    attempt_id: str
    attempt_no: int
    status: str
    slot_no: int
    start_step: int
    end_step: int | None = None
    started_at: str
    ended_at: str | None = None
    stop_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log: LogMetadataResponse


class AttemptListResponse(StrictModel):
    run_id: str
    default_attempt_id: str | None = None
    items: list[AttemptResponse]


class LogRecordResponse(StrictModel):
    timestamp: str | None = None
    level: str
    message: str
    event: Any = None


class LogWindowResponse(StrictModel):
    run_id: str
    attempt_id: str | None = None
    attempt_no: int | None = None
    job_id: str | None = None
    cursor: int
    next_cursor: int
    content: str
    records: list[LogRecordResponse]
    starts_mid_line: bool
    ends_mid_line: bool
    size_bytes: int
    file_id: str
    eof: bool
    terminal: bool


class ModelTracePageResponse(StrictModel):
    run_id: str
    attempt_id: str
    attempt_no: int
    items: list[dict[str, Any]]
    next_cursor: int
    eof: bool
    available: bool


class ModelTraceDetailResponse(StrictModel):
    run_id: str
    attempt_id: str
    event_seq: int
    trace_id: str
    trace: dict[str, Any]
    payload_available: bool
    cursor: int
    next_cursor: int | None = None
    size_bytes: int
    file_id: str | None = None
    content: str | None = None
    eof: bool


class ValidationResponse(StrictModel):
    code: str
    reason: str | None = None


class CheckpointItemResponse(StrictModel):
    step_no: int
    database_marker: bool
    retained: bool
    validated: bool
    status: str
    attempt_id: str | None = None
    virtual_time: str | None = None
    bundle_sha256: str | None = None
    size_bytes: int
    file_count: int
    resumable: bool
    validation: ValidationResponse | None = None


class CheckpointListResponse(StrictModel):
    run_id: str
    run_status: str
    recoverable_step: int
    can_resume: bool
    items: list[CheckpointItemResponse]


class CheckpointDetailResponse(CheckpointItemResponse):
    run_id: str
    bundle: dict[str, Any]
    agent_state: dict[str, Any]
    conversations: dict[str, Any]
    storage: dict[str, Any]
    files: list[dict[str, Any]]
    preview_sections: list[str]


class CheckpointPreviewResponse(StrictModel):
    run_id: str
    step_no: int
    section: str
    cursor: int
    next_cursor: int | None = None
    content: str
    size_bytes: int
    file_id: str
    eof: bool
