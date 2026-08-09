"""Add reusable public maps and immutable map revisions.

Revision ID: 0007_public_world_maps
Revises: 0006_agent_decision_context
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_public_world_maps"
down_revision: Union[str, Sequence[str], None] = "0006_agent_decision_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "world_maps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("map_key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_world_maps_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_world_maps_row_version"),
    )
    op.create_index("ix_world_maps_updated_at", "world_maps", ["updated_at", "id"])

    op.create_table(
        "world_map_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "map_id",
            sa.String(36),
            sa.ForeignKey("world_maps.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "base_revision_id",
            sa.String(36),
            sa.ForeignKey("world_map_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("world_json", sa.JSON(), nullable=False),
        sa.Column("world_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("map_id", "revision_no", name="uq_world_map_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_world_map_revision_number"),
        sa.CheckConstraint(
            "state IN ('DRAFT','PUBLISHED')", name="ck_world_map_revision_state"
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_world_map_revision_schema"),
        sa.CheckConstraint("lock_version >= 1", name="ck_world_map_revision_lock"),
    )
    op.create_index(
        "uq_world_map_one_draft",
        "world_map_revisions",
        ["map_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "ix_world_map_revisions_map",
        "world_map_revisions",
        ["map_id", "revision_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_world_map_revisions_map", table_name="world_map_revisions")
    op.drop_index("uq_world_map_one_draft", table_name="world_map_revisions")
    op.drop_table("world_map_revisions")
    op.drop_index("ix_world_maps_updated_at", table_name="world_maps")
    op.drop_table("world_maps")
