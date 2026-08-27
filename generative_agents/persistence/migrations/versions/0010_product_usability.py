"""Add product management, model health, archive and comparison state.

Revision ID: 0010_product_usability
Revises: 0009_prompt_workflow_ux
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_product_usability"
down_revision: Union[str, Sequence[str], None] = "0009_prompt_workflow_ux"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    with op.batch_alter_table("experiments") as batch:
        batch.add_column(sa.Column("owner", sa.String(length=120), nullable=False, server_default=""))
        batch.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("template_key", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_experiments_archived_updated_at", ["archived_at", "updated_at"])

    op.create_table(
        "model_probe_statuses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("draft_revision_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("resolved_model", sa.String(length=512), nullable=True),
        sa.Column("configuration_hash", sa.String(length=64), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reason_message", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("service_json", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_revision_id"], ["experiment_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("experiment_id", "purpose", name="uq_model_probe_experiment_purpose"),
        sa.CheckConstraint("purpose IN ('chat','embedding')", name="ck_model_probe_purpose"),
        sa.CheckConstraint("status IN ('UNTESTED','CHECKING','ONLINE','OFFLINE','STALE')", name="ck_model_probe_status"),
    )
    op.create_index("ix_model_probe_status_checked", "model_probe_statuses", ["status", "checked_at"])

    op.create_table(
        "experiment_saved_views",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("share_key", sa.String(length=36), nullable=False, unique=True),
        sa.Column("query_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "experiment_comparison_groups",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("experiment_ids_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_table("experiment_comparison_groups")
    op.drop_table("experiment_saved_views")
    op.drop_index("ix_model_probe_status_checked", table_name="model_probe_statuses")
    op.drop_table("model_probe_statuses")
    with op.batch_alter_table("experiments") as batch:
        batch.drop_index("ix_experiments_archived_updated_at")
        batch.drop_column("archived_at")
        batch.drop_column("template_key")
        batch.drop_column("tags")
        batch.drop_column("owner")
