"""Store uploaded Agent images directly in the database.

Revision ID: 0011_database_agent_images
Revises: 0010_product_usability
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_database_agent_images"
down_revision: Union[str, Sequence[str], None] = "0010_product_usability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("content_blob", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("content_blob")
