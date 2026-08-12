"""Add versioned capability scenario templates.

Revision ID: 0022_scenario_templates
Revises: 0021_experiment_capability_assembly
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0022_scenario_templates"
down_revision = "0021_experiment_capability_assembly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("current_draft_revision_id", sa.String(length=36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_scenario_templates_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_scenario_templates_row_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key"),
    )
    op.create_index(
        "ix_scenario_templates_updated", "scenario_templates", ["updated_at", "id"]
    )
    op.create_table(
        "scenario_template_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("base_revision_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=48), nullable=False),
        sa.Column("contract_json", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision_no >= 1", name="ck_scenario_template_revision_number"),
        sa.CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_scenario_template_revision_state"),
        sa.CheckConstraint("lock_version >= 1", name="ck_scenario_template_revision_lock"),
        sa.ForeignKeyConstraint(["base_revision_id"], ["scenario_template_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["template_id"], ["scenario_templates.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "revision_no", name="uq_scenario_template_revision_number"),
    )
    op.create_index(
        "uq_scenario_template_one_draft",
        "scenario_template_revisions",
        ["template_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "ix_scenario_template_revisions_template",
        "scenario_template_revisions",
        ["template_id", "revision_no"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scenario_template_revisions_template",
        table_name="scenario_template_revisions",
    )
    op.drop_index(
        "uq_scenario_template_one_draft",
        table_name="scenario_template_revisions",
    )
    op.drop_table("scenario_template_revisions")
    op.drop_index("ix_scenario_templates_updated", table_name="scenario_templates")
    op.drop_table("scenario_templates")
