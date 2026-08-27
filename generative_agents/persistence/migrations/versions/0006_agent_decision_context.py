"""Project structured Agent decision context for product-facing inspection.

Revision ID: 0006_agent_decision_context
Revises: 0005_artifact_source_identity
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_agent_decision_context"
down_revision: Union[str, Sequence[str], None] = "0005_artifact_source_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    op.add_column(
        "run_agent_steps",
        sa.Column(
            "decision_context_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_column("run_agent_steps", "decision_context_json")
