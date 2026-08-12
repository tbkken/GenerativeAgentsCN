"""Add public Agent templates and versioned crowds.

Revision ID: 0016_crowd_templates
Revises: 0015_brain_templates
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_crowd_templates"
down_revision: Union[str, Sequence[str], None] = "0015_brain_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(240), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_agent_templates_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_agent_templates_row_version"),
    )
    op.create_index("ix_agent_templates_updated_at", "agent_templates", ["updated_at", "id"])
    op.create_table(
        "agent_template_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agent_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("base_revision_id", sa.String(36), sa.ForeignKey("agent_template_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("agent_id", "revision_no", name="uq_agent_template_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_agent_template_revision_number"),
        sa.CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_agent_template_revision_state"),
        sa.CheckConstraint("schema_version >= 1", name="ck_agent_template_revision_schema"),
        sa.CheckConstraint("lock_version >= 1", name="ck_agent_template_revision_lock"),
    )
    op.create_index("uq_agent_template_one_draft", "agent_template_revisions", ["agent_id"], unique=True, sqlite_where=sa.text("state = 'DRAFT'"))
    op.create_index("ix_agent_template_revisions_agent", "agent_template_revisions", ["agent_id", "revision_no"])
    op.create_table(
        "crowd_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("crowd_key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_crowd_templates_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_crowd_templates_row_version"),
    )
    op.create_index("ix_crowd_templates_updated_at", "crowd_templates", ["updated_at", "id"])
    op.create_table(
        "crowd_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("crowd_id", sa.String(36), sa.ForeignKey("crowd_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("base_revision_id", sa.String(36), sa.ForeignKey("crowd_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("membership_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("crowd_id", "revision_no", name="uq_crowd_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_crowd_revision_number"),
        sa.CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_crowd_revision_state"),
        sa.CheckConstraint("lock_version >= 1", name="ck_crowd_revision_lock"),
    )
    op.create_index("uq_crowd_one_draft", "crowd_revisions", ["crowd_id"], unique=True, sqlite_where=sa.text("state = 'DRAFT'"))
    op.create_index("ix_crowd_revisions_crowd", "crowd_revisions", ["crowd_id", "revision_no"])
    op.create_table(
        "crowd_revision_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("crowd_id", sa.String(36), sa.ForeignKey("crowd_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("crowd_revision_id", sa.String(36), sa.ForeignKey("crowd_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agent_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("agent_revision_id", sa.String(36), sa.ForeignKey("agent_template_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("crowd_revision_id", "agent_id", name="uq_crowd_revision_agent"),
        sa.UniqueConstraint("crowd_revision_id", "position", name="uq_crowd_revision_position"),
        sa.CheckConstraint("position >= 0", name="ck_crowd_revision_member_position"),
    )
    op.create_index("ix_crowd_revision_members_revision", "crowd_revision_members", ["crowd_revision_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_crowd_revision_members_revision", table_name="crowd_revision_members")
    op.drop_table("crowd_revision_members")
    op.drop_index("ix_crowd_revisions_crowd", table_name="crowd_revisions")
    op.drop_index("uq_crowd_one_draft", table_name="crowd_revisions")
    op.drop_table("crowd_revisions")
    op.drop_index("ix_crowd_templates_updated_at", table_name="crowd_templates")
    op.drop_table("crowd_templates")
    op.drop_index("ix_agent_template_revisions_agent", table_name="agent_template_revisions")
    op.drop_index("uq_agent_template_one_draft", table_name="agent_template_revisions")
    op.drop_table("agent_template_revisions")
    op.drop_index("ix_agent_templates_updated_at", table_name="agent_templates")
    op.drop_table("agent_templates")
