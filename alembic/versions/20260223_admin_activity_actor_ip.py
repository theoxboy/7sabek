"""admin activity actor ip

Revision ID: 20260223_admin_activity_actor_ip
Revises: 20260222_admin_activity_actor_email
Create Date: 2026-02-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260223_admin_activity_actor_ip"
down_revision = "20260222_admin_activity_actor_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admin_activity_logs",
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_activity_logs", "actor_ip")
