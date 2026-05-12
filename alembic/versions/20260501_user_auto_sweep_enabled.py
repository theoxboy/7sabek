"""add auto_sweep_enabled to users

Revision ID: 20260501_user_auto_sweep_enabled
Revises: 20260501_distribution_backfill_and_sweep_uniqueness
Create Date: 2026-05-01 17:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260501_user_auto_sweep_enabled"
down_revision = "20260501_distribution_backfill_and_sweep_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auto_sweep_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "auto_sweep_enabled")
