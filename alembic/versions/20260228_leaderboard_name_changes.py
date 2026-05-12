"""leaderboard name change limits

Revision ID: 20260228_leaderboard_name_changes
Revises: 20260227_leaderboard_pseudo_required
Create Date: 2026-02-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260228_leaderboard_name_changes"
down_revision = "20260227_leaderboard_pseudo_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leaderboard_name_changes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_name", sa.String(length=40), nullable=True),
        sa.Column("new_name", sa.String(length=40), nullable=False),
        sa.Column("changed_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_leaderboard_name_changes_user_month",
        "leaderboard_name_changes",
        ["user_id", "changed_on"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_leaderboard_name_changes_user_month",
        table_name="leaderboard_name_changes",
    )
    op.drop_table("leaderboard_name_changes")
