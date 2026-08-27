"""Add database-owned artifact job log paths.

Revision ID: 0004_run_observability
Revises: 0003_builtin_catalog
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_run_observability"
down_revision: Union[str, Sequence[str], None] = "0003_builtin_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    # Nullable by design.  A pre-upgrade job has no trustworthy database-owned
    # path and is reported as LOG_UNAVAILABLE instead of being fabricated.
    op.add_column("artifact_jobs", sa.Column("log_path", sa.Text(), nullable=True))


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    op.drop_column("artifact_jobs", "log_path")
