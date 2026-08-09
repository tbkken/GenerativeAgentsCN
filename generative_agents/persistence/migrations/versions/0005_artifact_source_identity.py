"""Lock artifact jobs and artifacts to source steps and generators.

Revision ID: 0005_artifact_source_identity
Revises: 0004_run_observability
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_artifact_source_identity"
down_revision: Union[str, Sequence[str], None] = "0004_run_observability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_artifact_jobs_one_active", table_name="artifact_jobs")
    with op.batch_alter_table("artifact_jobs", recreate="always") as batch:
        batch.add_column(
            sa.Column("source_step", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "generator_version",
                sa.String(64),
                nullable=False,
                server_default="legacy-v1",
            )
        )
        batch.add_column(
            sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.drop_constraint("ck_artifact_jobs_type", type_="check")
        batch.create_check_constraint(
            "ck_artifact_jobs_type",
            "job_type IN ('BUILD_REPLAY','BUILD_REPORT','RESULT_BUNDLE',"
            "'FILTERED_MEMORIES','FILTERED_CONVERSATIONS','CHECKPOINT_BUNDLE')",
        )
        batch.create_check_constraint(
            "ck_artifact_jobs_source_step", "source_step >= 0"
        )
    op.create_index(
        "uq_artifact_jobs_one_active",
        "artifact_jobs",
        [
            "run_id",
            "job_type",
            "parameters_hash",
            "source_step",
            "generator_version",
        ],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED','RUNNING')"),
    )

    with op.batch_alter_table("run_artifacts", recreate="always") as batch:
        batch.add_column(
            sa.Column("source_step", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.drop_constraint("uq_run_artifact_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_run_artifact_identity",
            [
                "run_id",
                "artifact_type",
                "logical_name",
                "generator_version",
                "source_step",
            ],
        )
        batch.create_check_constraint(
            "ck_run_artifact_source_step", "source_step >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("run_artifacts", recreate="always") as batch:
        batch.drop_constraint("ck_run_artifact_source_step", type_="check")
        batch.drop_constraint("uq_run_artifact_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_run_artifact_identity",
            ["run_id", "artifact_type", "logical_name", "generator_version"],
        )
        batch.drop_column("partial")
        batch.drop_column("source_step")

    op.drop_index("uq_artifact_jobs_one_active", table_name="artifact_jobs")
    with op.batch_alter_table("artifact_jobs", recreate="always") as batch:
        batch.drop_constraint("ck_artifact_jobs_source_step", type_="check")
        batch.drop_constraint("ck_artifact_jobs_type", type_="check")
        batch.create_check_constraint(
            "ck_artifact_jobs_type",
            "job_type IN ('BUILD_REPLAY','RESULT_BUNDLE','FILTERED_MEMORIES',"
            "'FILTERED_CONVERSATIONS','CHECKPOINT_BUNDLE')",
        )
        batch.drop_column("partial")
        batch.drop_column("generator_version")
        batch.drop_column("source_step")
    op.create_index(
        "uq_artifact_jobs_one_active",
        "artifact_jobs",
        ["run_id", "job_type", "parameters_hash"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED','RUNNING')"),
    )
