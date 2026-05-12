"""platform settings announcement type

Revision ID: 20260216_platform_settings_announcement_type
Revises: 20260215_platform_settings_announcement
Create Date: 2026-02-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260216_platform_settings_announcement_type"
down_revision = "20260215_platform_settings_announcement"
branch_labels = None
depends_on = None


DEFAULT_TYPE = "custom"


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcement_type",
            sa.String(length=40),
            nullable=False,
            server_default=DEFAULT_TYPE,
        ),
    )
    op.alter_column("platform_settings", "announcement_type", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "announcement_type")
