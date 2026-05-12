"""add advisor tab toggle to platform settings

Revision ID: 20260507_platform_settings_advisor_tab_toggle
Revises: 20260501_distribution_config_versioning
Create Date: 2026-05-07 14:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260507_platform_settings_advisor_tab_toggle"
down_revision = "20260501_distribution_config_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column("advisor_tab_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("platform_settings", "advisor_tab_enabled")
