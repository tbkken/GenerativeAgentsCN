"""OpenAPI response contracts for Run observability and checkpoints."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from generative_agents.config.schema import StrictModel


class LogMetadataResponse(StrictModel):
    """日志文件身份、大小、终态和截断状态。"""

    available: bool
    size_bytes: int = 0
    file_id: str | None = None
    modified_at_ns: int | None = None
    terminal: bool = False


class AttemptResponse(StrictModel):
    """单个 Run Attempt 的生命周期与日志入口。"""

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
    """一个 Run 的全部 Attempt 响应集合。"""

    run_id: str
    default_attempt_id: str | None = None
    items: list[AttemptResponse]


class LogRecordResponse(StrictModel):
    """带字节范围和时间信息的一条完整日志记录。"""

    timestamp: str | None = None
    level: str
    message: str
    event: Any = None


class LogWindowResponse(StrictModel):
    """UTF-8 安全日志窗口、游标和文件身份。"""

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
    """模型追踪记录的稳定游标分页响应。"""

    run_id: str
    attempt_id: str
    attempt_no: int
    items: list[dict[str, Any]]
    next_cursor: int
    eof: bool
    available: bool


class ModelTraceDetailResponse(StrictModel):
    """单次逻辑模型调用的尝试、用量和可选脱敏负载。"""

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
    """通用完整性校验结果和失败原因。"""

    code: str
    reason: str | None = None


class CheckpointItemResponse(StrictModel):
    """检查点列表项的步骤、状态、大小和校验信息。"""

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
    """Run 的保留、已裁剪和可恢复检查点集合。"""

    run_id: str
    run_status: str
    recoverable_step: int
    can_resume: bool
    items: list[CheckpointItemResponse]


class CheckpointDetailResponse(CheckpointItemResponse):
    """检查点列表信息及其内部成员摘要。"""

    run_id: str
    bundle: dict[str, Any]
    agent_state: dict[str, Any]
    conversations: dict[str, Any]
    storage: dict[str, Any]
    files: list[dict[str, Any]]
    preview_sections: list[str]


class CheckpointPreviewResponse(StrictModel):
    """检查点某个文本成员的有界字节预览。"""

    run_id: str
    step_no: int
    section: str
    cursor: int
    next_cursor: int | None = None
    content: str
    size_bytes: int
    file_id: str
    eof: bool
