"""add force onboarding v2 review flag

Revision ID: 20260329_user_force_onboarding_v2_review
Revises: 20260309_user_shiftpilot_states
Create Date: 2026-03-29 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260329_user_force_onboarding_v2_review"
down_revision = "20260309_user_shiftpilot_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "force_onboarding_v2_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "force_onboarding_v2_review")
