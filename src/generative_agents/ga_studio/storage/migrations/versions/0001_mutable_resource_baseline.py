"""Create the Studio-owned author-resource and package-catalog schema.

Revision ID: 0001_mutable_resource_baseline
Revises: none

Historical databases are intentionally unsupported. The product owns one clean
baseline matching the current SQLAlchemy model and may rebuild local data.
"""

from alembic import op

from generative_agents.ga_studio.storage.models import Asset
from generative_agents.ga_studio.storage.models import Base
from generative_agents.ga_studio.storage.models import Secret
from generative_agents.ga_studio.storage.models import SeedResourceTombstone
from generative_agents.ga_studio.storage.models import SpatialAssetDefinition
from generative_agents.ga_studio.storage.models import StudioAgent
from generative_agents.ga_studio.storage.models import StudioCrowd
from generative_agents.ga_studio.storage.models import StudioEvaluator
from generative_agents.ga_studio.storage.models import StudioModelPreset
from generative_agents.ga_studio.storage.models import StudioPackageCatalog
from generative_agents.ga_studio.storage.models import StudioSkill
from generative_agents.ga_studio.storage.models import WorldMap


revision = "0001_mutable_resource_baseline"
down_revision = None
branch_labels = None
depends_on = None


STUDIO_TABLES = [
    SeedResourceTombstone.__table__,
    StudioSkill.__table__,
    StudioAgent.__table__,
    StudioCrowd.__table__,
    StudioModelPreset.__table__,
    StudioEvaluator.__table__,
    WorldMap.__table__,
    SpatialAssetDefinition.__table__,
    Secret.__table__,
    Asset.__table__,
    StudioPackageCatalog.__table__,
]


def upgrade() -> None:
    """Create only Studio author resources and the rebuildable package index."""

    Base.metadata.create_all(bind=op.get_bind(), tables=STUDIO_TABLES, checkfirst=False)


def downgrade() -> None:
    """Drop the clean baseline when explicitly requested in development."""

    Base.metadata.drop_all(bind=op.get_bind(), tables=STUDIO_TABLES, checkfirst=True)
