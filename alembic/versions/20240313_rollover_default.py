"""set rollover_enabled default false

Revision ID: 20240313_rollover_default
Revises: 20240313_add_swept_at
Create Date: 2024-03-13 00:00:03.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240313_rollover_default"
down_revision = "20240313_add_swept_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "envelopes",
        "rollover_enabled",
        server_default=sa.text("false"),
    )


def downgrade() -> None:
    op.alter_column(
        "envelopes",
        "rollover_enabled",
        server_default=sa.text("true"),
    )
