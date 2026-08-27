"""Add reusable Agent-orchestration brain templates.

Revision ID: 0015_brain_templates
Revises: 0014_llm_context_inputs
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_brain_templates"
down_revision: Union[str, Sequence[str], None] = "0014_llm_context_inputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    op.create_table(
        "brain_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brain_key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_draft_revision_id", sa.String(36), nullable=True),
        sa.Column("current_published_revision_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')", name="ck_brain_templates_status"),
        sa.CheckConstraint("row_version >= 1", name="ck_brain_templates_row_version"),
    )
    op.create_index("ix_brain_templates_updated_at", "brain_templates", ["updated_at", "id"])
    op.create_table(
        "brain_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brain_id", sa.String(36), sa.ForeignKey("brain_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("base_revision_id", sa.String(36), sa.ForeignKey("brain_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompts_json", sa.JSON(), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("brain_id", "revision_no", name="uq_brain_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_brain_revision_number"),
        sa.CheckConstraint("state IN ('DRAFT','PUBLISHED')", name="ck_brain_revision_state"),
        sa.CheckConstraint("schema_version >= 1", name="ck_brain_revision_schema"),
        sa.CheckConstraint("lock_version >= 1", name="ck_brain_revision_lock"),
    )
    op.create_index("uq_brain_one_draft", "brain_revisions", ["brain_id"], unique=True, sqlite_where=sa.text("state = 'DRAFT'"))
    op.create_index("ix_brain_revisions_brain", "brain_revisions", ["brain_id", "revision_no"])
    op.create_table(
        "brain_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brain_id", sa.String(36), sa.ForeignKey("brain_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("brain_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("workflow_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("revision_id", "workflow_key", name="uq_brain_revision_workflow_key"),
    )
    op.create_index("ix_brain_workflows_brain_revision", "brain_workflows", ["brain_id", "revision_id"])


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_index("ix_brain_workflows_brain_revision", table_name="brain_workflows")
    op.drop_table("brain_workflows")
    op.drop_index("ix_brain_revisions_brain", table_name="brain_revisions")
    op.drop_index("uq_brain_one_draft", table_name="brain_revisions")
    op.drop_table("brain_revisions")
    op.drop_index("ix_brain_templates_updated_at", table_name="brain_templates")
    op.drop_table("brain_templates")
