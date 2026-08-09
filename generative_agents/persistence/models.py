"""Core SQLAlchemy 2.0 mappings.

Business state transitions belong in services/runtime. This module only declares
storage shape and database-enforced isolation invariants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class BuiltinCatalogSnapshot(Base):
    """Immutable creation-time source for new built-in experiment Drafts."""

    __tablename__ = "builtin_catalog_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("ix_builtin_catalog_created_at", "created_at", "id"),)


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    experiment_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    current_draft_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "experiment_revisions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_experiments_current_draft",
        ),
        nullable=True,
    )
    current_published_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "experiment_revisions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_experiments_current_published",
        ),
        nullable=True,
    )
    latest_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "runs.id", ondelete="SET NULL", use_alter=True, name="fk_experiments_latest_run"
        ),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','QUEUED','RUNNING','PAUSED','COMPLETED','CANCELLED','FAILED')",
            name="ck_experiments_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_experiments_row_version"),
        Index("ix_experiments_updated_at", "updated_at"),
        Index("ix_experiments_status_updated_at", "status", "updated_at"),
    )


class ExperimentRevision(Base):
    __tablename__ = "experiment_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    base_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("experiment_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validated_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    snapshot_complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("experiment_id", "revision_no", name="uq_revision_number"),
        CheckConstraint("revision_no >= 1", name="ck_revision_number"),
        CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_revision_state"),
        CheckConstraint("schema_version >= 1", name="ck_revision_schema_version"),
        CheckConstraint("lock_version >= 1", name="ck_revision_lock_version"),
        Index(
            "uq_revision_one_draft_per_experiment",
            "experiment_id",
            unique=True,
            sqlite_where=text("state = 'DRAFT'"),
        ),
        Index("ix_revisions_experiment", "experiment_id", "revision_no"),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="RESTRICT"), nullable=False
    )
    revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiment_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    slot_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recoverable_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stride_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    virtual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid_create_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_dir: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("slot_no IS NULL OR slot_no > 0", name="ck_runs_positive_slot"),
        CheckConstraint("start_step >= 0", name="ck_runs_start_step"),
        CheckConstraint("requested_steps >= 1", name="ck_runs_requested_steps"),
        CheckConstraint("completed_steps >= 0", name="ck_runs_completed_steps"),
        CheckConstraint("recoverable_step >= 0", name="ck_runs_recoverable_step"),
        CheckConstraint("stride_minutes >= 1", name="ck_runs_stride_minutes"),
        CheckConstraint("resume_count >= 0", name="ck_runs_resume_count"),
        CheckConstraint(
            "status IN ('QUEUED','STARTING','RUNNING','PAUSE_REQUESTED','PAUSED',"
            "'CANCEL_REQUESTED','CANCELLED','COMPLETED','FAILED','INTERRUPTED')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "((status IN ('STARTING','RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')) "
            "AND slot_no IS NOT NULL AND current_attempt_id IS NOT NULL) OR "
            "((status NOT IN ('STARTING','RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')) "
            "AND slot_no IS NULL AND current_attempt_id IS NULL)",
            name="ck_runs_slot_attempt_by_status",
        ),
        CheckConstraint(
            "(status IN ('RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED') "
            "AND pid IS NOT NULL AND pid_create_time IS NOT NULL) OR "
            "(status NOT IN ('RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED'))",
            name="ck_runs_pid_by_status",
        ),
        Index(
            "uq_runs_slot_no",
            "slot_no",
            unique=True,
            sqlite_where=text("slot_no IS NOT NULL"),
        ),
        Index(
            "uq_runs_one_open_per_experiment",
            "experiment_id",
            unique=True,
            sqlite_where=text(
                "status IN ('QUEUED','STARTING','RUNNING','PAUSE_REQUESTED','PAUSED','CANCEL_REQUESTED')"
            ),
        ),
        Index("ix_runs_experiment_created", "experiment_id", "created_at"),
        Index("ix_runs_status_queued", "status", "queued_at"),
    )


class RunQueue(Base):
    __tablename__ = "run_queue"
    __table_args__ = (
        CheckConstraint("reason IN ('NEW','RESUME','RETRY')", name="ck_run_queue_reason"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="SPAWNING")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid_create_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_step: Mapped[int] = mapped_column(Integer, nullable=False)
    end_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "attempt_no", name="uq_run_attempt_number"),
        CheckConstraint("attempt_no >= 1", name="ck_run_attempt_number"),
        CheckConstraint("slot_no >= 1", name="ck_run_attempt_slot"),
        CheckConstraint("start_step >= 1", name="ck_run_attempt_start_step"),
        CheckConstraint("end_step IS NULL OR end_step >= 0", name="ck_run_attempt_end_step"),
        CheckConstraint("status IN ('SPAWNING','RUNNING','ENDED')", name="ck_run_attempt_status"),
        Index("ix_run_attempts_run", "run_id", "attempt_no"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("ix_run_events_run_id_id", "run_id", "id"),
        Index("ix_run_events_created_at", "created_at"),
        {"sqlite_autoincrement": True},
    )


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    rewrapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('OPENAI_API_KEY','GENERIC_TOKEN')", name="ck_secrets_kind"),
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_assets_size"),
        Index("ix_assets_sha256", "sha256"),
    )


class LegacyImport(Base):
    __tablename__ = "legacy_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_path", "source_fingerprint", name="uq_legacy_import_source"
        ),
    )


class RunArtifact(Base):
    __tablename__ = "run_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial: Mapped[bool] = mapped_column(nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "artifact_type",
            "logical_name",
            "generator_version",
            "source_step",
            name="uq_run_artifact_identity",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_run_artifact_size"),
        CheckConstraint("source_step >= 0", name="ck_run_artifact_source_step"),
        CheckConstraint("source_kind IN ('RAW','DERIVED')", name="ck_run_artifact_source"),
        CheckConstraint(
            "state IN ('BUILDING','READY','FAILED','STALE')", name="ck_run_artifact_state"
        ),
        Index("ix_run_artifacts_run", "run_id", "state"),
    )


class ArtifactJob(Base):
    __tablename__ = "artifact_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    parameters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generator_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy-v1"
    )
    partial: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid_create_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The database is the authority for worker log ownership.  Existing jobs
    # created before the observability migration intentionally keep this NULL:
    # deriving a path later would claim that a log exists when it may not.
    log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("run_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "job_type IN ('BUILD_REPLAY','RESULT_BUNDLE','FILTERED_MEMORIES',"
            "'FILTERED_CONVERSATIONS','CHECKPOINT_BUNDLE','BUILD_REPORT')",
            name="ck_artifact_jobs_type",
        ),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_artifact_jobs_status",
        ),
        CheckConstraint("attempt_no >= 0", name="ck_artifact_jobs_attempt"),
        CheckConstraint("source_step >= 0", name="ck_artifact_jobs_source_step"),
        CheckConstraint("progress >= 0 AND progress <= 1", name="ck_artifact_jobs_progress"),
        Index(
            "uq_artifact_jobs_one_active",
            "run_id",
            "job_type",
            "parameters_hash",
            "source_step",
            "generator_version",
            unique=True,
            sqlite_where=text("status IN ('QUEUED','RUNNING')"),
        ),
        Index("ix_artifact_jobs_status_created", "status", "created_at"),
        Index("ix_artifact_jobs_run", "run_id", "created_at"),
    )


class RunResultSummary(Base):
    __tablename__ = "run_result_summaries"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    available_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    virtual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    conversation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    memory_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    model_call_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    model_retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    result_state: Mapped[str] = mapped_column(String(24), nullable=False, default="EMPTY")
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    projection_version: Mapped[str] = mapped_column(String(32), nullable=False)
    result_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_frame_sha256: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("available_step >= 0", name="ck_result_summary_available_step"),
        CheckConstraint("result_version >= 0", name="ck_result_summary_version"),
        CheckConstraint(
            "result_state IN ('EMPTY','PARTIAL','COMPLETE','CORRUPTED')",
            name="ck_result_summary_state",
        ),
    )


class RunStep(Base):
    __tablename__ = "run_steps"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    step_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    virtual_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frame_path: Mapped[str] = mapped_column(Text, nullable=False)
    frame_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    movement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_accessed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_logical_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    model_retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint: Mapped[bool] = mapped_column(nullable=False, default=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("step_no >= 1", name="ck_run_steps_step"),
        Index("ix_run_steps_run_time", "run_id", "virtual_time"),
    )


class RunAgentStep(Base):
    __tablename__ = "run_agent_steps"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    step_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    virtual_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    action_emoji: Mapped[str | None] = mapped_column(String(32))
    activity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    currently_text: Mapped[str | None] = mapped_column(Text)
    schedule_item_id: Mapped[str | None] = mapped_column(String(120))
    path_source: Mapped[str] = mapped_column(String(16), nullable=False, default="OBSERVED")
    decision_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_no"], ["run_steps.run_id", "run_steps.step_no"], ondelete="CASCADE"
        ),
        Index("ix_agent_steps_run_step", "run_id", "step_no"),
        Index("ix_agent_steps_run_agent_step", "run_id", "agent_key", "step_no"),
        Index("ix_agent_steps_run_time", "run_id", "virtual_time"),
    )


class RunAgentSummary(Base):
    __tablename__ = "run_agent_summaries"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    currently_text: Mapped[str | None] = mapped_column(Text)
    action_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    movement_steps: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    conversation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    memory_created_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rest_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chat_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    moving_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    other_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latest_schedule_revision_id: Mapped[str | None] = mapped_column(String(36))
    updated_step: Mapped[int] = mapped_column(Integer, nullable=False)


class RunRelationshipEdge(Base):
    __tablename__ = "run_relationship_edges"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    agent_a: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_b: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    first_conversation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_conversation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("agent_a < agent_b", name="ck_relationship_agent_order"),
    )


class RunScheduleRevision(Base):
    __tablename__ = "run_schedule_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_key: Mapped[str] = mapped_column(String(80), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_step: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(36))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "agent_key", "revision_no", name="uq_schedule_revision_no"),
        UniqueConstraint("run_id", "agent_key", "content_hash", name="uq_schedule_content"),
        Index("ix_schedule_run_agent_step", "run_id", "agent_key", "effective_step"),
    )


class RunDomainEvent(Base):
    __tablename__ = "run_domain_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    virtual_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    primary_agent_key: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    importance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    source_type: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(36))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_domain_events_run_step", "run_id", "step_no"),)


class RunDomainEventAgent(Base):
    __tablename__ = "run_domain_event_agents"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_domain_events.id", ondelete="CASCADE"), primary_key=True
    )
    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)

    __table_args__ = (Index("ix_domain_event_agents_lookup", "run_id", "agent_key", "event_id"),)


class RunConversation(Base):
    __tablename__ = "run_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    start_step: Mapped[int] = mapped_column(Integer, nullable=False)
    end_step: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    duration_source: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    initiator_agent_key: Mapped[str] = mapped_column(String(80), nullable=False)
    responder_agent_key: Mapped[str] = mapped_column(String(80), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    ended_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_conversations_run_started", "run_id", "started_at"),)


class RunConversationParticipant(Base):
    __tablename__ = "run_conversation_participants"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_conversations.id", ondelete="CASCADE"), primary_key=True
    )
    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)

    __table_args__ = (Index("ix_conversation_participants_agent", "run_id", "agent_key"),)


class RunMessage(Base):
    __tablename__ = "run_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_agent_key: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_step: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_no", name="uq_message_sequence"),
        Index("ix_messages_run_speaker_time", "run_id", "speaker_agent_key", "observed_at"),
    )


class RunMemoryEvent(Base):
    __tablename__ = "run_memory_events"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    memory_node_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    poignancy: Mapped[float | None] = mapped_column(Float)
    created_step: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_step: Mapped[int | None] = mapped_column(Integer)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_step: Mapped[int | None] = mapped_column(Integer)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_node_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("ix_memory_events_run_agent_created", "run_id", "agent_key", "created_step"),
        Index("ix_memory_events_run_state", "run_id", "state"),
    )


class RunModelUsage(Base):
    __tablename__ = "run_model_usage"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    resolved_model: Mapped[str] = mapped_column(String(255), primary_key=True)
    logical_call_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    successful_call_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fallback_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    physical_attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    latency_buckets_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    max_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_step: Mapped[int | None] = mapped_column(Integer)


class RunModelTraceCursor(Base):
    __tablename__ = "run_model_trace_cursors"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    last_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    byte_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
