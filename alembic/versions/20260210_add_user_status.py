"""add user status

Revision ID: 20260210_add_user_status
Revises: 20260209_add_page_views
Create Date: 2026-02-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260210_add_user_status"
down_revision = "20260209_add_page_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "status")
