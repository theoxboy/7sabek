"""platform settings message placements

Revision ID: 20260217_platform_settings_message_placements
Revises: 20260216_platform_settings_announcement_type
Create Date: 2026-02-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_platform_settings_message_placements"
down_revision = "20260216_platform_settings_announcement_type"
branch_labels = None
depends_on = None


DEFAULT_PLACEMENTS = (
    "[\"global_sticky\",\"global_popup\",\"global_footer\","
    "\"landing\",\"login\",\"register\",\"app_header\"]"
)


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "maintenance_placements",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_PLACEMENTS}'::json"),
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_placements",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_PLACEMENTS}'::json"),
        ),
    )
    op.alter_column("platform_settings", "maintenance_placements", server_default=None)
    op.alter_column("platform_settings", "announcement_placements", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "announcement_placements")
    op.drop_column("platform_settings", "maintenance_placements")
