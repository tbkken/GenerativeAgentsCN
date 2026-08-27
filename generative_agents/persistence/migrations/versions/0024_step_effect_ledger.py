"""Add the canonical per-step effect ledger.

Revision ID: 0024_step_effect_ledger
Revises: 0023_skill_brain_cutover
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_step_effect_ledger"
down_revision = "0023_skill_brain_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    op.create_table(
        "run_step_effects",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("effect_id", sa.String(length=36), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("virtual_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effect_type", sa.String(length=40), nullable=False),
        sa.Column("primary_agent_key", sa.String(length=80), nullable=True),
        sa.Column("agent_keys_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_effect_id", sa.String(length=36), nullable=True),
        sa.Column("skill_name", sa.String(length=80), nullable=True),
        sa.Column("skill_revision", sa.String(length=64), nullable=True),
        sa.Column("call_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id", "step_no"],
            ["run_steps.run_id", "run_steps.step_no"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "effect_id"),
    )
    op.create_index(
        "ix_step_effects_run_step",
        "run_step_effects",
        ["run_id", "step_no", "sequence_no"],
        unique=False,
    )
    op.create_index(
        "ix_step_effects_run_type",
        "run_step_effects",
        ["run_id", "effect_type", "step_no"],
        unique=False,
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_index("ix_step_effects_run_type", table_name="run_step_effects")
    op.drop_index("ix_step_effects_run_step", table_name="run_step_effects")
    op.drop_table("run_step_effects")
