"""Retired workflow data rewrite retained only as a migration-chain marker.

Revision ID: 0014_llm_context_inputs
Revises: 0013_explicit_prompt_paths
"""

revision = "0014_llm_context_inputs"
down_revision = "0013_explicit_prompt_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    pass


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    pass
