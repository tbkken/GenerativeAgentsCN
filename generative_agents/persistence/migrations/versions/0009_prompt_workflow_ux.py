"""Upgrade untouched draft schedule workflows to the branched runtime graph.

Revision ID: 0009_prompt_workflow_ux
Revises: 0008_prompt_workflows
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

from generative_agents.config import make_default_workflows, workflow_hash


revision: str = "0009_prompt_workflow_ux"
down_revision: Union[str, Sequence[str], None] = "0008_prompt_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_SCHEDULE_NODES = [
    "start",
    "prepare_context",
    "prompt_base_desc",
    "prompt_retrieve_plan",
    "prompt_retrieve_thought",
    "prompt_retrieve_currently",
    "prompt_wake_up",
    "prompt_schedule_init",
    "prompt_schedule_daily",
    "prompt_schedule_decompose",
    "prompt_schedule_revise",
    "end",
]


def _is_untouched_linear_schedule(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return False
    return (
        [node.get("node_id") for node in nodes] == _LEGACY_SCHEDULE_NODES
        and len(edges) == len(nodes) - 1
        and not any(node.get("kind") in {"switch", "if_else"} for node in nodes)
    )


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    workflows = sa.Table("experiment_workflows", metadata, autoload_with=bind)
    versions = sa.Table("experiment_workflow_versions", metadata, autoload_with=bind)
    revisions = sa.Table("experiment_revisions", metadata, autoload_with=bind)
    experiments = sa.Table("experiments", metadata, autoload_with=bind)

    default_hash = (
        sa.select(versions.c.workflow_hash)
        .where(
            versions.c.experiment_id == workflows.c.experiment_id,
            versions.c.workflow_key == "schedule",
            versions.c.is_default.is_(True),
        )
        .scalar_subquery()
    )
    rows = list(bind.execute(
        sa.select(
            workflows.c.id,
            workflows.c.experiment_id,
            workflows.c.revision_id,
            workflows.c.definition_json,
            workflows.c.workflow_hash,
            revisions.c.definition_json.label("experiment_definition"),
            default_hash.label("default_hash"),
        )
        .join(revisions, revisions.c.id == workflows.c.revision_id)
        .where(
            workflows.c.workflow_key == "schedule",
            revisions.c.state == "DRAFT",
        )
    ).mappings())

    upgraded = make_default_workflows()["schedule"]
    upgraded_json = upgraded.model_dump(mode="json", exclude_none=False)
    upgraded_hash = workflow_hash(upgraded)
    prompt_keys = [
        node.prompt_key
        for node in upgraded.nodes
        if node.kind == "llm" and node.prompt_key is not None
    ]
    now = datetime.now(timezone.utc)

    for row in rows:
        if row["workflow_hash"] != row["default_hash"]:
            continue
        if not _is_untouched_linear_schedule(row["definition_json"]):
            continue
        experiment_definition = row["experiment_definition"]
        prompts = experiment_definition.get("prompts", {})
        prompt_snapshot = {key: prompts[key] for key in prompt_keys if key in prompts}
        next_version = (
            bind.scalar(
                sa.select(sa.func.max(versions.c.version_no)).where(
                    versions.c.experiment_id == row["experiment_id"],
                    versions.c.workflow_key == "schedule",
                )
            )
            or 0
        ) + 1
        bind.execute(
            workflows.update()
            .where(workflows.c.id == row["id"])
            .values(
                definition_json=upgraded_json,
                workflow_hash=upgraded_hash,
                updated_at=now,
            )
        )
        bind.execute(
            versions.insert().values(
                id=str(uuid4()),
                experiment_id=row["experiment_id"],
                workflow_key="schedule",
                version_no=next_version,
                label="升级日程分支编排",
                definition_json=upgraded_json,
                prompt_contents_json=prompt_snapshot,
                workflow_hash=upgraded_hash,
                is_default=False,
                source_revision_id=row["revision_id"],
                created_at=now,
            )
        )
        bind.execute(
            revisions.update()
            .where(revisions.c.id == row["revision_id"])
            .values(
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
    # Workflow versions are deliberately immutable.  Keep the safe, upgraded
    # draft graph and its restore point instead of deleting user-visible history.
    pass
