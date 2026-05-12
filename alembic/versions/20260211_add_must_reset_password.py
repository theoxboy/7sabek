"""add must reset password

Revision ID: 20260211_add_must_reset_password
Revises: 20260210_add_user_status
Create Date: 2026-02-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260211_add_must_reset_password"
down_revision = "20260210_add_user_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_reset_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_reset_password")
