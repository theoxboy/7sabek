"""add swept_at to envelope_periods

Revision ID: 20240313_add_swept_at
Revises: 20240313_add_user_email
Create Date: 2024-03-13 00:00:02.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240313_add_swept_at"
down_revision = "20240313_add_user_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "envelope_periods",
        sa.Column("swept_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("envelope_periods", "swept_at")
