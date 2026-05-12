"""add updated_at to category_envelope_map

Revision ID: 20240314_category_env_map_updated_at
Revises: 20240313_env_move_tx_nullable
Create Date: 2024-03-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20240314_cat_env_map_upd_at"
down_revision = "20240313_env_move_tx_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "category_envelope_map",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("category_envelope_map", "updated_at")
