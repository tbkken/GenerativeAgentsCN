"""Replace editable Prompt aliases with explicit LLM input paths.

Revision ID: 0013_explicit_prompt_paths
Revises: 0012_global_workflow_functions
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from generative_agents.config import ExperimentDefinition, definition_hash
from generative_agents.config.prompt_variables import canonicalize_prompt_payload


revision: str = "0013_explicit_prompt_paths"
down_revision: Union[str, Sequence[str], None] = "0012_global_workflow_functions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """应用当前版本的数据库结构升级，按顺序创建或调整所需对象。"""
    bind = op.get_bind()
    metadata = sa.MetaData()
    revisions = sa.Table("experiment_revisions", metadata, autoload_with=bind)
    experiments = sa.Table("experiments", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc)

    rows = list(
        bind.execute(
            sa.select(
                revisions.c.id,
                revisions.c.experiment_id,
                revisions.c.definition_json,
            ).where(revisions.c.state == "DRAFT")
        ).mappings()
    )
    for row in rows:
        payload = dict(row["definition_json"])
        current_prompts = payload.get("prompts", {})
        canonical_prompts = canonicalize_prompt_payload(current_prompts)
        if canonical_prompts == current_prompts:
            continue
        payload["prompts"] = canonical_prompts
        definition = ExperimentDefinition.model_validate(payload)
        bind.execute(
            revisions.update()
            .where(revisions.c.id == row["id"])
            .values(
                definition_json=definition.model_dump(mode="json", exclude_none=False),
                definition_hash=definition_hash(definition),
                validation_json=None,
                validated_hash=None,
                lock_version=revisions.c.lock_version + 1,
                updated_at=now,
            )
        )
        bind.execute(
            experiments.update()
            .where(experiments.c.id == row["experiment_id"])
            .values(updated_at=now)
        )


def downgrade() -> None:
    """回滚当前版本的数据库结构升级，按依赖逆序移除所增对象。"""
    # Explicit paths are semantically stronger and must not be flattened back
    # into ambiguous root aliases.
    pass
