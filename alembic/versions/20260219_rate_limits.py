"""rate limits

Revision ID: 20260219_rate_limits
Revises: 20260218_platform_settings_announcement_schedule_targeting
Create Date: 2026-02-19 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260219_rate_limits"
down_revision = "20260218_platform_settings_announcement_schedule_targeting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_login_max",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_login_window_minutes",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_register_max",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_register_window_minutes",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_api_max",
            sa.Integer(),
            nullable=False,
            server_default="120",
        ),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "rate_limit_api_window_minutes",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.alter_column("platform_settings", "rate_limit_login_max", server_default=None)
    op.alter_column(
        "platform_settings", "rate_limit_login_window_minutes", server_default=None
    )
    op.alter_column("platform_settings", "rate_limit_register_max", server_default=None)
    op.alter_column(
        "platform_settings", "rate_limit_register_window_minutes", server_default=None
    )
    op.alter_column("platform_settings", "rate_limit_api_max", server_default=None)
    op.alter_column(
        "platform_settings", "rate_limit_api_window_minutes", server_default=None
    )

    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(length=200), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("rate_limit_buckets", "count", server_default=None)


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
    op.drop_column("platform_settings", "rate_limit_api_window_minutes")
    op.drop_column("platform_settings", "rate_limit_api_max")
    op.drop_column("platform_settings", "rate_limit_register_window_minutes")
    op.drop_column("platform_settings", "rate_limit_register_max")
    op.drop_column("platform_settings", "rate_limit_login_window_minutes")
    op.drop_column("platform_settings", "rate_limit_login_max")
