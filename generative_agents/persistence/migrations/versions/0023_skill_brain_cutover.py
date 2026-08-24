"""Remove the retired Capability/Workflow platform tables.

Revision ID: 0023_skill_brain_cutover
Revises: 0022_scenario_templates
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_skill_brain_cutover"
down_revision = "0022_scenario_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SKILL.md files are now the only source of agent behavior. These tables
    # belonged to the retired typed capability/workflow implementation.
    retired_tables = (
        "legacy_imports",
        "scenario_template_revisions",
        "scenario_templates",
        "experiment_revision_capabilities",
        "brain_revision_extensions",
        "agent_revision_extensions",
        "brain_workflows",
        "brain_revisions",
        "brain_templates",
        "capability_bundle_revisions",
        "capability_bundles",
        "capability_revisions",
        "capability_definitions",
        "workflow_functions",
        "experiment_workflow_versions",
        "experiment_workflows",
    )
    connection = op.get_bind()
    existing = set(sa.inspect(connection).get_table_names())
    sqlite = connection.dialect.name == "sqlite"
    if sqlite and "experiment_revisions" in existing:
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_published_revision_no_update")
    try:
        for table_name in ("experiment_revisions", "builtin_catalog_snapshots"):
            if table_name in existing:
                connection.execute(
                    sa.text(
                        f"UPDATE {table_name} "
                        "SET definition_json = json_remove(definition_json, '$.prompts') "
                        "WHERE json_type(definition_json, '$.prompts') IS NOT NULL"
                    )
                )
    finally:
        if sqlite and "experiment_revisions" in existing:
            connection.exec_driver_sql(
                """
                CREATE TRIGGER trg_published_revision_no_update
                BEFORE UPDATE ON experiment_revisions
                WHEN OLD.state = 'PUBLISHED'
                BEGIN
                  SELECT RAISE(ABORT, 'PUBLISHED_REVISION_IMMUTABLE');
                END
                """
            )

    # Several retired catalogs referenced their active Revision while each
    # Revision also referenced its catalog owner. SQLite therefore sees a
    # deliberate foreign-key cycle. The complete subgraph is being discarded,
    # so disable FK enforcement only for this autocommitted DDL block and turn
    # it back on before continuing with the new schema.
    if sqlite:
        with op.get_context().autocommit_block():
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                for table_name in retired_tables:
                    if table_name in existing:
                        op.drop_table(table_name)
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        return

    for table_name in retired_tables:
        if table_name in existing:
            op.drop_table(table_name)


def downgrade() -> None:
    raise RuntimeError("The SKILL brain cutover is intentionally irreversible")
