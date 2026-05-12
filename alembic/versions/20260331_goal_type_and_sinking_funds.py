"""add goal type for sinking funds

Revision ID: 20260331_goal_type_and_sinking_funds
Revises: 20260329_user_force_onboarding_v2_review
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260331_goal_type_and_sinking_funds"
down_revision = "20260329_user_force_onboarding_v2_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column("goal_type", sa.String(length=24), nullable=False, server_default="goal"),
    )
    op.execute("UPDATE goals SET goal_type = 'goal' WHERE goal_type IS NULL")
    op.alter_column("goals", "goal_type", server_default=None)


def downgrade() -> None:
    op.drop_column("goals", "goal_type")
