"""Create the capability-composition simulation schema from scratch.

Revision ID: 0001_capability_composition
Revises: none

Historical databases are intentionally unsupported. The product owns one clean
baseline matching the current SQLAlchemy model and may rebuild local data.
"""

from alembic import op

from generative_agents.persistence.models import Base


revision = "0001_capability_composition"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create every current table, index, constraint, and relationship."""

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)
    op.execute(
        """
        CREATE TRIGGER trg_skill_revision_no_update
        BEFORE UPDATE ON skill_revisions
        BEGIN
          SELECT RAISE(ABORT, 'SKILL_REVISION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_published_revision_no_update
        BEFORE UPDATE ON experiment_revisions
        WHEN OLD.state = 'PUBLISHED'
          AND NOT EXISTS (
            SELECT 1 FROM resource_deletion_grants grant_row
            WHERE grant_row.resource_type = 'experiment'
              AND grant_row.resource_id = OLD.experiment_id
          )
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_REVISION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_published_revision_no_delete
        BEFORE DELETE ON experiment_revisions
        WHEN OLD.state = 'PUBLISHED'
          AND NOT EXISTS (
            SELECT 1 FROM resource_deletion_grants grant_row
            WHERE grant_row.resource_type = 'experiment'
              AND grant_row.resource_id = OLD.experiment_id
          )
        BEGIN
          SELECT RAISE(ABORT, 'PUBLISHED_REVISION_IMMUTABLE');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_requires_published_revision
        BEFORE INSERT ON runs
        WHEN NOT EXISTS (
          SELECT 1 FROM experiment_revisions revision
          WHERE revision.id = NEW.revision_id
            AND revision.state = 'PUBLISHED'
        )
        BEGIN
          SELECT RAISE(ABORT, 'RUN_REQUIRES_PUBLISHED_REVISION');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_queue_requires_queued_run
        BEFORE INSERT ON run_queue
        WHEN NOT EXISTS (
          SELECT 1 FROM runs run
          WHERE run.id = NEW.run_id AND run.status = 'QUEUED'
        )
        BEGIN
          SELECT RAISE(ABORT, 'RUN_QUEUE_REQUIRES_QUEUED_RUN');
        END
        """
    )


def downgrade() -> None:
    """Drop the clean baseline when explicitly requested in development."""

    op.execute("DROP TRIGGER IF EXISTS trg_run_queue_requires_queued_run")
    op.execute("DROP TRIGGER IF EXISTS trg_skill_revision_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_run_requires_published_revision")
    op.execute("DROP TRIGGER IF EXISTS trg_published_revision_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_published_revision_no_update")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
