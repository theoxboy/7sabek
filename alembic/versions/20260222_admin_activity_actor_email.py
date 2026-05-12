"""admin activity actor email

Revision ID: 20260222_admin_activity_actor_email
Revises: 20260221_admin_activity_logs
Create Date: 2026-02-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260222_admin_activity_actor_email"
down_revision = "20260221_admin_activity_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admin_activity_logs",
        sa.Column("actor_email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_activity_logs", "actor_email")
