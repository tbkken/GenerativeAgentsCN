"""Add versioned tools and optional Agent capability extensions.

Revision ID: 0019_tool_and_agent_extensions
Revises: 0018_spatial_asset_platform
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0019_tool_and_agent_extensions"
down_revision = "0018_spatial_asset_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    op.create_table(
        "tool_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tool_kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("current_draft_revision_id", sa.String(length=36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tool_kind IN ('CAR','BICYCLE','MOTORCYCLE','ACCESS_CARD','DEVICE','OTHER')",
            name="ck_tool_definitions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','PUBLISHED')", name="ck_tool_definitions_status"
        ),
        sa.CheckConstraint(
            "row_version >= 1", name="ck_tool_definitions_version"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_key"),
    )
    op.create_index(
        "ix_tool_definitions_updated",
        "tool_definitions",
        ["updated_at", "id"],
    )
    op.create_table(
        "tool_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_id", sa.String(length=36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("base_revision_id", sa.String(length=36), nullable=True),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("contract_json", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("lock_version >= 1", name="ck_tool_revision_lock"),
        sa.CheckConstraint("revision_no >= 1", name="ck_tool_revision_number"),
        sa.CheckConstraint(
            "state IN ('DRAFT','PUBLISHED')", name="ck_tool_revision_state"
        ),
        sa.ForeignKeyConstraint(
            ["base_revision_id"], ["tool_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"], ["tool_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_id", "revision_no", name="uq_tool_revision_number"),
    )
    op.create_index(
        "ix_tool_revisions_tool", "tool_revisions", ["tool_id", "revision_no"]
    )
    op.create_index(
        "uq_tool_one_draft",
        "tool_revisions",
        ["tool_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_table(
        "agent_revision_extensions",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("extension_json", sa.JSON(), nullable=False),
        sa.Column("extension_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agent_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["agent_template_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("revision_id"),
    )
    op.create_index(
        "ix_agent_revision_extensions_agent",
        "agent_revision_extensions",
        ["agent_id", "revision_id"],
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_index(
        "ix_agent_revision_extensions_agent", table_name="agent_revision_extensions"
    )
    op.drop_table("agent_revision_extensions")
    op.drop_index("uq_tool_one_draft", table_name="tool_revisions")
    op.drop_index("ix_tool_revisions_tool", table_name="tool_revisions")
    op.drop_table("tool_revisions")
    op.drop_index("ix_tool_definitions_updated", table_name="tool_definitions")
    op.drop_table("tool_definitions")
