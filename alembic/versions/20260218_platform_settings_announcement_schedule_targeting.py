"""platform settings announcement schedule targeting

Revision ID: 20260218_platform_settings_announcement_schedule_targeting
Revises: 20260217_platform_settings_message_placements
Create Date: 2026-02-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260218_platform_settings_announcement_schedule_targeting"
down_revision = "20260217_platform_settings_message_placements"
branch_labels = None
depends_on = None


DEFAULT_ROLES = "[\"any\"]"
DEFAULT_STATUSES = "[\"any\"]"
DEFAULT_COUNTRIES = "[]"


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column("announcement_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_settings",
        sa.Column("announcement_end_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_timezone",
            sa.String(length=64),
            nullable=False,
            server_default="UTC",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_recurrence",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_roles",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_ROLES}'::json"),
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_statuses",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_STATUSES}'::json"),
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_countries",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_COUNTRIES}'::json"),
        ),
    )
    op.alter_column("platform_settings", "announcement_timezone", server_default=None)
    op.alter_column("platform_settings", "announcement_recurrence", server_default=None)
    op.alter_column("platform_settings", "announcement_roles", server_default=None)
    op.alter_column("platform_settings", "announcement_statuses", server_default=None)
    op.alter_column("platform_settings", "announcement_countries", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "announcement_countries")
    op.drop_column("platform_settings", "announcement_statuses")
    op.drop_column("platform_settings", "announcement_roles")
    op.drop_column("platform_settings", "announcement_recurrence")
    op.drop_column("platform_settings", "announcement_timezone")
    op.drop_column("platform_settings", "announcement_end_at")
    op.drop_column("platform_settings", "announcement_start_at")
