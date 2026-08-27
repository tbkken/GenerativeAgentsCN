"""Add optional capability composition to brain revisions.

Revision ID: 0020_brain_capability_extensions
Revises: 0019_tool_and_agent_extensions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0020_brain_capability_extensions"
down_revision = "0019_tool_and_agent_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    op.create_table(
        "brain_revision_extensions",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("brain_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("extension_json", sa.JSON(), nullable=False),
        sa.Column("extension_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brain_id"], ["brain_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["brain_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("revision_id"),
    )
    op.create_index(
        "ix_brain_revision_extensions_brain",
        "brain_revision_extensions",
        ["brain_id", "revision_id"],
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_index(
        "ix_brain_revision_extensions_brain",
        table_name="brain_revision_extensions",
    )
    op.drop_table("brain_revision_extensions")
