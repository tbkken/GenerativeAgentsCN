"""Add immutable built-in catalog snapshots.

Revision ID: 0003_builtin_catalog
Revises: 0002_results
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_builtin_catalog"
down_revision: Union[str, Sequence[str], None] = "0002_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "builtin_catalog_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("source_manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_builtin_catalog_created_at",
        "builtin_catalog_snapshots",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_builtin_catalog_created_at", table_name="builtin_catalog_snapshots")
    op.drop_table("builtin_catalog_snapshots")
