"""platform settings maintenance message

Revision ID: 20260214_platform_settings_maintenance_message
Revises: 20260213_platform_settings
Create Date: 2026-02-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260214_platform_settings_maintenance_message"
down_revision = "20260213_platform_settings"
branch_labels = None
depends_on = None


DEFAULT_MESSAGE = "Plateforme en maintenance. Réessayez plus tard."


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "maintenance_message",
            sa.String(length=500),
            nullable=False,
            server_default=DEFAULT_MESSAGE,
        ),
    )
    op.alter_column("platform_settings", "maintenance_message", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "maintenance_message")
