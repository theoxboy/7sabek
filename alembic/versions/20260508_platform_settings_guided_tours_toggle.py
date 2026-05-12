"""add guided tours toggle to platform settings

Revision ID: 20260508_platform_settings_guided_tours_toggle
Revises: 20260508_user_force_tour_replay_version
Create Date: 2026-05-08 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260508_platform_settings_guided_tours_toggle"
down_revision = "20260508_user_force_tour_replay_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column("guided_tours_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("platform_settings", "guided_tours_enabled")
