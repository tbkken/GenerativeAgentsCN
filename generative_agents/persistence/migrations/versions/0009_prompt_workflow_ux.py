"""Retired workflow data rewrite retained only as a migration-chain marker.

Revision ID: 0009_prompt_workflow_ux
Revises: 0008_prompt_workflows
"""

revision = "0009_prompt_workflow_ux"
down_revision = "0008_prompt_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    pass


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    pass
