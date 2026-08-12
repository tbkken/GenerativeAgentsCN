"""Add optional capability-composed experiment assemblies.

Revision ID: 0021_experiment_capability_assembly
Revises: 0020_brain_capability_extensions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0021_experiment_capability_assembly"
down_revision = "0020_brain_capability_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_revision_capabilities",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=48), nullable=False),
        sa.Column("extension_json", sa.JSON(), nullable=False),
        sa.Column("extension_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["experiment_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("revision_id"),
    )
    op.create_index(
        "ix_experiment_revision_capabilities_experiment",
        "experiment_revision_capabilities",
        ["experiment_id", "revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_revision_capabilities_experiment",
        table_name="experiment_revision_capabilities",
    )
    op.drop_table("experiment_revision_capabilities")
