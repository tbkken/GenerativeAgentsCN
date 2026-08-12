"""Add versioned reusable capabilities and capability bundles.

Revision ID: 0017_capability_platform
Revises: 0016_crowd_templates
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_capability_platform"
down_revision: Union[str, Sequence[str], None] = "0016_crowd_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _versioned_container(
    table: str,
    *,
    key_column: str,
    status_constraint: str,
    row_version_constraint: str,
) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(key_column, sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name=status_constraint),
        sa.CheckConstraint("row_version >= 1", name=row_version_constraint),
    )


def upgrade() -> None:
    _versioned_container(
        "capability_definitions",
        key_column="capability_key",
        status_constraint="ck_capability_definitions_status",
        row_version_constraint="ck_capability_definitions_row_version",
    )
    op.create_index(
        "ix_capability_definitions_updated",
        "capability_definitions",
        ["updated_at", "id"],
    )
    op.create_table(
        "capability_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "capability_id",
            sa.String(36),
            sa.ForeignKey("capability_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "base_revision_id",
            sa.String(36),
            sa.ForeignKey("capability_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "schema_version",
            sa.String(40),
            nullable=False,
            server_default="ga-capability/v1",
        ),
        sa.Column("contract_json", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "capability_id", "revision_no", name="uq_capability_revision_number"
        ),
        sa.CheckConstraint("revision_no >= 1", name="ck_capability_revision_number"),
        sa.CheckConstraint(
            "state IN ('DRAFT','PUBLISHED')", name="ck_capability_revision_state"
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_capability_revision_lock"),
    )
    op.create_index(
        "uq_capability_one_draft",
        "capability_revisions",
        ["capability_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "ix_capability_revisions_capability",
        "capability_revisions",
        ["capability_id", "revision_no"],
    )

    _versioned_container(
        "capability_bundles",
        key_column="bundle_key",
        status_constraint="ck_capability_bundles_status",
        row_version_constraint="ck_capability_bundles_row_version",
    )
    op.create_index(
        "ix_capability_bundles_updated",
        "capability_bundles",
        ["updated_at", "id"],
    )
    op.create_table(
        "capability_bundle_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "bundle_id",
            sa.String(36),
            sa.ForeignKey("capability_bundles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "base_revision_id",
            sa.String(36),
            sa.ForeignKey("capability_bundle_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "schema_version",
            sa.String(40),
            nullable=False,
            server_default="ga-capability-bundle/v1",
        ),
        sa.Column("composition_json", sa.JSON(), nullable=False),
        sa.Column("composition_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "bundle_id", "revision_no", name="uq_capability_bundle_revision_number"
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_capability_bundle_revision_number"
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','PUBLISHED')",
            name="ck_capability_bundle_revision_state",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_capability_bundle_revision_lock"
        ),
    )
    op.create_index(
        "uq_capability_bundle_one_draft",
        "capability_bundle_revisions",
        ["bundle_id"],
        unique=True,
        sqlite_where=sa.text("state = 'DRAFT'"),
    )
    op.create_index(
        "ix_capability_bundle_revisions_bundle",
        "capability_bundle_revisions",
        ["bundle_id", "revision_no"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_bundle_revisions_bundle",
        table_name="capability_bundle_revisions",
    )
    op.drop_index(
        "uq_capability_bundle_one_draft",
        table_name="capability_bundle_revisions",
    )
    op.drop_table("capability_bundle_revisions")
    op.drop_index("ix_capability_bundles_updated", table_name="capability_bundles")
    op.drop_table("capability_bundles")
    op.drop_index(
        "ix_capability_revisions_capability", table_name="capability_revisions"
    )
    op.drop_index("uq_capability_one_draft", table_name="capability_revisions")
    op.drop_table("capability_revisions")
    op.drop_index(
        "ix_capability_definitions_updated", table_name="capability_definitions"
    )
    op.drop_table("capability_definitions")
