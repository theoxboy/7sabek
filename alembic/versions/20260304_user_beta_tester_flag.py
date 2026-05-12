"""add user beta tester flag

Revision ID: 20260304_user_beta_tester_flag
Revises: 20260303_platform_multi_announcements
Create Date: 2026-03-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260304_user_beta_tester_flag"
down_revision = "20260303_platform_multi_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_beta_tester",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_beta_tester")
