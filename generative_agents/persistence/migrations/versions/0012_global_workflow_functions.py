"""Add globally reusable custom workflow Functions.

Revision ID: 0012_global_workflow_functions
Revises: 0011_database_agent_images
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_global_workflow_functions"
down_revision: Union[str, Sequence[str], None] = "0011_database_agent_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_functions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("function_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_type", sa.String(length=120), nullable=False, server_default="any"),
        sa.Column("output_type", sa.String(length=120), nullable=False, server_default="any"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_workflow_function_row_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("function_key"),
    )
    op.create_index(
        "ix_workflow_functions_updated",
        "workflow_functions",
        ["updated_at", "function_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_functions_updated", table_name="workflow_functions")
    op.drop_table("workflow_functions")
