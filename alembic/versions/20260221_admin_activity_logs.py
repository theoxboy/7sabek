"""admin activity logs

Revision ID: 20260221_admin_activity_logs
Revises: 20260220_login_throttle
Create Date: 2026-02-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260221_admin_activity_logs"
down_revision = "20260220_login_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_activity_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=400), nullable=False),
    )
    op.create_index(
        "ix_admin_activity_logs_id",
        "admin_activity_logs",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_activity_logs_id", table_name="admin_activity_logs")
    op.drop_table("admin_activity_logs")
