"""platform settings announcement

Revision ID: 20260215_platform_settings_announcement
Revises: 20260214_platform_settings_maintenance_message
Create Date: 2026-02-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260215_platform_settings_announcement"
down_revision = "20260214_platform_settings_maintenance_message"
branch_labels = None
depends_on = None


DEFAULT_ANNOUNCEMENT = ""


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_message",
            sa.String(length=500),
            nullable=False,
            server_default=DEFAULT_ANNOUNCEMENT,
        ),
    )
    op.alter_column("platform_settings", "announcement_enabled", server_default=None)
    op.alter_column("platform_settings", "announcement_message", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "announcement_message")
    op.drop_column("platform_settings", "announcement_enabled")
