"""extend alembic_version length

Revision ID: 20260115_alembicver_len
Revises: 20240314_category_env_map_updated_at
Create Date: 2026-01-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260115_alembicver_len"
down_revision = "20240314_cat_env_map_upd_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        type_=sa.String(length=255),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        type_=sa.String(length=32),
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
