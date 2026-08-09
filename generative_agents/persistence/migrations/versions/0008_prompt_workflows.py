"""Add revision-owned Prompt workflows and immutable restore points.

Revision ID: 0008_prompt_workflows
Revises: 0007_public_world_maps
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_prompt_workflows"
down_revision: Union[str, Sequence[str], None] = "0007_public_world_maps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            sa.String(36),
            sa.ForeignKey("experiment_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("workflow_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("revision_id", "workflow_key", name="uq_revision_workflow_key"),
    )
    op.create_index(
        "ix_workflows_experiment_revision",
        "experiment_workflows",
        ["experiment_id", "revision_id"],
    )

    op.create_table(
        "experiment_workflow_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("prompt_contents_json", sa.JSON(), nullable=False),
        sa.Column("workflow_hash", sa.String(64), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "source_revision_id",
            sa.String(36),
            sa.ForeignKey("experiment_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "experiment_id",
            "workflow_key",
            "version_no",
            name="uq_experiment_workflow_version",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_workflow_version_number"),
    )
    op.create_index(
        "uq_experiment_workflow_default",
        "experiment_workflow_versions",
        ["experiment_id", "workflow_key"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_index(
        "ix_workflow_versions_experiment",
        "experiment_workflow_versions",
        ["experiment_id", "workflow_key", "version_no"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_published_workflow_no_update
        BEFORE UPDATE ON experiment_workflows
        WHEN (SELECT state FROM experiment_revisions WHERE id = OLD.revision_id) = 'PUBLISHED'
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_WORKFLOW_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_published_workflow_no_delete
        BEFORE DELETE ON experiment_workflows
        WHEN (SELECT state FROM experiment_revisions WHERE id = OLD.revision_id) = 'PUBLISHED'
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_WORKFLOW_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workflow_version_no_update
        BEFORE UPDATE ON experiment_workflow_versions
        BEGIN
          SELECT RAISE(ABORT, 'WORKFLOW_VERSION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workflow_version_no_delete
        BEFORE DELETE ON experiment_workflow_versions
        BEGIN
          SELECT RAISE(ABORT, 'WORKFLOW_VERSION_IMMUTABLE');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_workflow_version_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_workflow_version_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_published_workflow_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_published_workflow_no_update")
    op.drop_index(
        "ix_workflow_versions_experiment", table_name="experiment_workflow_versions"
    )
    op.drop_index(
        "uq_experiment_workflow_default", table_name="experiment_workflow_versions"
    )
    op.drop_table("experiment_workflow_versions")
    op.drop_index("ix_workflows_experiment_revision", table_name="experiment_workflows")
    op.drop_table("experiment_workflows")
