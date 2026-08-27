"""Create the isolated experiment core schema.

Revision ID: 0001_core
Revises: none
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_core"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    # SQLite permits forward FK references, which lets the three intentionally
    # cyclic ownership pointers remain database-enforced without nullable staging tables.
    op.create_table(
        "experiment_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("base_revision_id", sa.String(36), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("validated_hash", sa.String(64), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_complete", sa.Boolean(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_revision_id"], ["experiment_revisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("experiment_id", "revision_no", name="uq_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_revision_number"),
        sa.CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_revision_state"),
        sa.CheckConstraint("schema_version >= 1", name="ck_revision_schema_version"),
        sa.CheckConstraint("lock_version >= 1", name="ck_revision_lock_version"),
    )
    op.create_index(
        "uq_revision_one_draft_per_experiment",
        "experiment_revisions",
        ["experiment_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "ix_revisions_experiment", "experiment_revisions", ["experiment_id", "revision_no"]
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("latest_run_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["current_draft_revision_id"],
            ["experiment_revisions.id"],
            name="fk_experiments_current_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_published_revision_id"],
            ["experiment_revisions.id"],
            name="fk_experiments_current_published",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_run_id"],
            ["runs.id"],
            name="fk_experiments_latest_run",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("experiment_key"),
        sa.CheckConstraint(
            "status IN ('DRAFT','QUEUED','RUNNING','PAUSED','COMPLETED','CANCELLED','FAILED')",
            name="ck_experiments_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_experiments_row_version"),
    )
    op.create_index("ix_experiments_updated_at", "experiments", ["updated_at"])
    op.create_index(
        "ix_experiments_status_updated_at", "experiments", ["status", "updated_at"]
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_step", sa.Integer(), nullable=False),
        sa.Column("requested_steps", sa.Integer(), nullable=False),
        sa.Column("completed_steps", sa.Integer(), nullable=False),
        sa.Column("recoverable_step", sa.Integer(), nullable=False),
        sa.Column("stride_minutes", sa.Integer(), nullable=False),
        sa.Column("virtual_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_attempt_id", sa.String(36), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("pid_create_time", sa.Float(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_dir", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("resume_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["experiment_revisions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("slot_no IS NULL OR slot_no > 0", name="ck_runs_positive_slot"),
        sa.CheckConstraint("start_step >= 0", name="ck_runs_start_step"),
        sa.CheckConstraint("requested_steps >= 1", name="ck_runs_requested_steps"),
        sa.CheckConstraint("completed_steps >= 0", name="ck_runs_completed_steps"),
        sa.CheckConstraint("recoverable_step >= 0", name="ck_runs_recoverable_step"),
        sa.CheckConstraint("stride_minutes >= 1", name="ck_runs_stride_minutes"),
        sa.CheckConstraint("resume_count >= 0", name="ck_runs_resume_count"),
        sa.CheckConstraint(
            "status IN ('QUEUED','STARTING','RUNNING','PAUSE_REQUESTED','PAUSED',"
            "'CANCEL_REQUESTED','CANCELLED','COMPLETED','FAILED','INTERRUPTED')",
            name="ck_runs_status",
        ),
        sa.CheckConstraint(
            "((status IN ('STARTING','RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')) "
            "AND slot_no IS NOT NULL AND current_attempt_id IS NOT NULL) OR "
            "((status NOT IN ('STARTING','RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')) "
            "AND slot_no IS NULL AND current_attempt_id IS NULL)",
            name="ck_runs_slot_attempt_by_status",
        ),
        sa.CheckConstraint(
            "(status IN ('RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED') "
            "AND pid IS NOT NULL AND pid_create_time IS NOT NULL) OR "
            "(status NOT IN ('RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED'))",
            name="ck_runs_pid_by_status",
        ),
    )
    op.create_index(
        "uq_runs_slot_no",
        "runs",
        ["slot_no"],
        unique=True,
        sqlite_where=sa.text("slot_no IS NOT NULL"),
    )
    op.create_index(
        "uq_runs_one_open_per_experiment",
        "runs",
        ["experiment_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('QUEUED','STARTING','RUNNING','PAUSE_REQUESTED','PAUSED','CANCEL_REQUESTED')"
        ),
    )
    op.create_index("ix_runs_experiment_created", "runs", ["experiment_id", "created_at"])
    op.create_index("ix_runs_status_queued", "runs", ["status", "queued_at"])

    op.create_table(
        "run_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id"),
        sa.CheckConstraint("reason IN ('NEW','RESUME','RETRY')", name="ck_run_queue_reason"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("pid_create_time", sa.Float(), nullable=True),
        sa.Column("log_path", sa.Text(), nullable=False),
        sa.Column("start_step", sa.Integer(), nullable=False),
        sa.Column("end_step", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_run_attempt_number"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_run_attempt_number"),
        sa.CheckConstraint("slot_no >= 1", name="ck_run_attempt_slot"),
        sa.CheckConstraint("start_step >= 1", name="ck_run_attempt_start_step"),
        sa.CheckConstraint("end_step IS NULL OR end_step >= 0", name="ck_run_attempt_end_step"),
        sa.CheckConstraint(
            "status IN ('SPAWNING','RUNNING','ENDED')", name="ck_run_attempt_status"
        ),
    )
    op.create_index("ix_run_attempts_run", "run_attempts", ["run_id", "attempt_no"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_run_events_run_id_id", "run_events", ["run_id", "id"])
    op.create_index("ix_run_events_created_at", "run_events", ["created_at"])

    op.create_table(
        "secrets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rewrapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["supersedes_id"], ["secrets.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "kind IN ('OPENAI_API_KEY','GENERIC_TOKEN')", name="ck_secrets_kind"
        ),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_assets_size"),
    )
    op.create_index("ix_assets_sha256", "assets", ["sha256"])
    op.create_table(
        "legacy_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_type", "source_path", "source_fingerprint", name="uq_legacy_import_source"
        ),
    )

    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("artifact_type", sa.String(48), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("generator_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "run_id",
            "artifact_type",
            "logical_name",
            "generator_version",
            name="uq_run_artifact_identity",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_run_artifact_size"),
        sa.CheckConstraint("source_kind IN ('RAW','DERIVED')", name="ck_run_artifact_source"),
        sa.CheckConstraint(
            "state IN ('BUILDING','READY','FAILED','STALE')", name="ck_run_artifact_state"
        ),
    )
    op.create_index("ix_run_artifacts_run", "run_artifacts", ["run_id", "state"])

    op.create_table(
        "artifact_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("job_type", sa.String(48), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("parameters_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_pid", sa.Integer(), nullable=True),
        sa.Column("pid_create_time", sa.Float(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["run_artifacts.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "job_type IN ('BUILD_REPLAY','RESULT_BUNDLE','FILTERED_MEMORIES',"
            "'FILTERED_CONVERSATIONS','CHECKPOINT_BUNDLE')",
            name="ck_artifact_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_artifact_jobs_status",
        ),
        sa.CheckConstraint("attempt_no >= 0", name="ck_artifact_jobs_attempt"),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name="ck_artifact_jobs_progress"),
    )
    op.create_index(
        "uq_artifact_jobs_one_active",
        "artifact_jobs",
        ["run_id", "job_type", "parameters_hash"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED','RUNNING')"),
    )
    op.create_index(
        "ix_artifact_jobs_status_created", "artifact_jobs", ["status", "created_at"]
    )
    op.create_index("ix_artifact_jobs_run", "artifact_jobs", ["run_id", "created_at"])

    op.execute(
        """
        CREATE TRIGGER trg_published_revision_no_update
        BEFORE UPDATE ON experiment_revisions
        WHEN OLD.state = 'PUBLISHED'
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_REVISION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_published_revision_no_delete
        BEFORE DELETE ON experiment_revisions
        WHEN OLD.state = 'PUBLISHED'
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_REVISION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_requires_published_revision
        BEFORE INSERT ON runs
        WHEN NOT EXISTS (
          SELECT 1 FROM experiment_revisions r
          WHERE r.id = NEW.revision_id AND r.state = 'PUBLISHED'
        )
        BEGIN
          SELECT RAISE(ABORT, 'RUN_REQUIRES_PUBLISHED_REVISION');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_queue_requires_queued_run
        BEFORE INSERT ON run_queue
        WHEN NOT EXISTS (SELECT 1 FROM runs r WHERE r.id = NEW.run_id AND r.status = 'QUEUED')
        BEGIN
          SELECT RAISE(ABORT, 'RUN_QUEUE_REQUIRES_QUEUED_RUN');
        END
        """
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.execute("DROP TRIGGER IF EXISTS trg_run_queue_requires_queued_run")
    op.execute("DROP TRIGGER IF EXISTS trg_run_requires_published_revision")
    op.execute("DROP TRIGGER IF EXISTS trg_published_revision_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_published_revision_no_update")
    op.drop_table("artifact_jobs")
    op.drop_table("run_artifacts")
    op.drop_table("legacy_imports")
    op.drop_table("assets")
    op.drop_table("secrets")
    op.drop_table("run_events")
    op.drop_table("run_attempts")
    op.drop_table("run_queue")
    op.drop_table("runs")
    op.drop_table("experiments")
    op.drop_table("experiment_revisions")
