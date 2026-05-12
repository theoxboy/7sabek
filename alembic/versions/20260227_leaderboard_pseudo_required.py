"""leaderboard pseudo and suspension

Revision ID: 20260227_leaderboard_pseudo_required
Revises: 20260226_gamification
Create Date: 2026-02-27 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260227_leaderboard_pseudo_required"
down_revision = "20260226_gamification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("leaderboard_name", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("suspended_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE user_gamification SET leaderboard_opt_in = true")
    op.alter_column(
        "user_gamification",
        "leaderboard_opt_in",
        server_default=sa.text("true"),
    )


def downgrade() -> None:
    op.alter_column(
        "user_gamification",
        "leaderboard_opt_in",
        server_default=sa.text("false"),
    )
    op.drop_column("users", "suspended_until")
    op.drop_column("users", "leaderboard_name")
