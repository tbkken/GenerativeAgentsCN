"""Add the runtime context input to legacy Draft LLM nodes.

Revision ID: 0014_llm_context_inputs
Revises: 0013_explicit_prompt_paths
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from generative_agents.config import (
    WorkflowDefinition,
    ensure_llm_context_inputs,
    workflow_hash,
)


revision: str = "0014_llm_context_inputs"
down_revision: Union[str, Sequence[str], None] = "0013_explicit_prompt_paths"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    workflows = sa.Table("experiment_workflows", metadata, autoload_with=bind)
    revisions = sa.Table("experiment_revisions", metadata, autoload_with=bind)
    experiments = sa.Table("experiments", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc)
    changed_revision_ids: set[str] = set()

    rows = list(
        bind.execute(
            sa.select(
                workflows.c.id,
                workflows.c.revision_id,
                workflows.c.definition_json,
            )
            .join(revisions, revisions.c.id == workflows.c.revision_id)
            .where(revisions.c.state == "DRAFT")
        ).mappings()
    )
    for row in rows:
        current = WorkflowDefinition.model_validate(row["definition_json"])
        normalized = ensure_llm_context_inputs(current)
        payload = normalized.model_dump(mode="json", exclude_none=False)
        if payload == row["definition_json"]:
            continue
        bind.execute(
            workflows.update()
            .where(workflows.c.id == row["id"])
            .values(
                definition_json=payload,
                workflow_hash=workflow_hash(normalized),
                updated_at=now,
            )
        )
        changed_revision_ids.add(row["revision_id"])

    for revision_id in changed_revision_ids:
        experiment_id = bind.scalar(
            sa.select(revisions.c.experiment_id).where(revisions.c.id == revision_id)
        )
        bind.execute(
            revisions.update()
            .where(revisions.c.id == revision_id)
            .values(
                validation_json=None,
                validated_hash=None,
                lock_version=revisions.c.lock_version + 1,
                updated_at=now,
            )
        )
        bind.execute(
            experiments.update()
            .where(experiments.c.id == experiment_id)
            .values(updated_at=now)
        )


def downgrade() -> None:
    # The optional context port is backward compatible and prevents restored
    # explicit Prompt paths from becoming invalid again.
    pass
