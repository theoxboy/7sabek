"""add user force tour replay version

Revision ID: 20260508_user_force_tour_replay_version
Revises: 20260507_platform_settings_advisor_tab_toggle
Create Date: 2026-05-08 16:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260508_user_force_tour_replay_version"
down_revision = "20260507_platform_settings_advisor_tab_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("force_tour_replay_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "force_tour_replay_version")
