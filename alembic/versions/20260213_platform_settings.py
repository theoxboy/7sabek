"""platform settings

Revision ID: 20260213_platform_settings
Revises: 20260211_add_must_reset_password
Create Date: 2026-02-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260213_platform_settings"
down_revision = "20260211_add_must_reset_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform_name", sa.String(length=120), nullable=False),
        sa.Column("support_email", sa.String(length=200), nullable=False),
        sa.Column("registration_enabled", sa.Boolean(), nullable=False),
        sa.Column("maintenance_mode", sa.Boolean(), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column("default_sweep_interval_days", sa.Integer(), nullable=False),
        sa.Column("password_min_length", sa.Integer(), nullable=False),
        sa.Column("default_auto_distribution_enabled", sa.Boolean(), nullable=False),
    )
    op.execute(
        """
        INSERT INTO platform_settings (
            id,
            platform_name,
            support_email,
            registration_enabled,
            maintenance_mode,
            default_currency,
            default_sweep_interval_days,
            password_min_length,
            default_auto_distribution_enabled
        )
        VALUES (
            1,
            'Floussy',
            'ELIDRYSSI@GMAIL.COM',
            true,
            false,
            'MAD',
            30,
            8,
            false
        )
        """
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
