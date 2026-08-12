"""Add versioned spatial asset definitions and revisions.

Revision ID: 0018_spatial_asset_platform
Revises: 0017_capability_platform
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0018_spatial_asset_platform"
down_revision = "0017_capability_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spatial_asset_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("asset_kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("current_draft_revision_id", sa.String(length=36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "asset_kind IN ('TILE','OBJECT','ZONE','MARKING','NETWORK')",
            name="ck_spatial_asset_definitions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','PUBLISHED')",
            name="ck_spatial_asset_definitions_status",
        ),
        sa.CheckConstraint(
            "row_version >= 1", name="ck_spatial_asset_definitions_version"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_key"),
    )
    op.create_index(
        "ix_spatial_asset_definitions_updated",
        "spatial_asset_definitions",
        ["updated_at", "id"],
    )
    op.create_table(
        "spatial_asset_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("spatial_asset_id", sa.String(length=36), nullable=False),
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
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_spatial_asset_revision_lock"
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_spatial_asset_revision_number"
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','PUBLISHED')", name="ck_spatial_asset_revision_state"
        ),
        sa.ForeignKeyConstraint(
            ["base_revision_id"], ["spatial_asset_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["spatial_asset_id"], ["spatial_asset_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spatial_asset_id", "revision_no", name="uq_spatial_asset_revision_number"
        ),
    )
    op.create_index(
        "ix_spatial_asset_revisions_asset",
        "spatial_asset_revisions",
        ["spatial_asset_id", "revision_no"],
    )
    op.create_index(
        "uq_spatial_asset_one_draft",
        "spatial_asset_revisions",
        ["spatial_asset_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )


def downgrade() -> None:
    op.drop_index("uq_spatial_asset_one_draft", table_name="spatial_asset_revisions")
    op.drop_index(
        "ix_spatial_asset_revisions_asset", table_name="spatial_asset_revisions"
    )
    op.drop_table("spatial_asset_revisions")
    op.drop_index(
        "ix_spatial_asset_definitions_updated", table_name="spatial_asset_definitions"
    )
    op.drop_table("spatial_asset_definitions")
