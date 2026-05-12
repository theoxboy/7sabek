"""login throttle

Revision ID: 20260220_login_throttle
Revises: 20260219_rate_limits
Create Date: 2026-02-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260220_login_throttle"
down_revision = "20260219_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("force_reset", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.alter_column("login_throttles", "failed_count", server_default=None)
    op.alter_column("login_throttles", "force_reset", server_default=None)


def downgrade() -> None:
    op.drop_table("login_throttles")
