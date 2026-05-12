"""gamification tables and transaction source

Revision ID: 20260226_gamification
Revises: 20260225_user_soft_delete_grace
Create Date: 2026-02-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260226_gamification"
down_revision = "20260225_user_soft_delete_grace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.alter_column("transactions", "source", server_default=None)

    op.create_table(
        "user_gamification",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("points_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_weekly", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_monthly", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "current_streak_days", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "longest_streak_days", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_activity_date", sa.Date(), nullable=True),
        sa.Column("freeze_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("freeze_week_start", sa.Date(), nullable=True),
        sa.Column("week_start", sa.Date(), nullable=True),
        sa.Column("month_start", sa.Date(), nullable=True),
        sa.Column("freeze_pending_date", sa.Date(), nullable=True),
        sa.Column("freeze_pending_streak", sa.Integer(), nullable=True),
        sa.Column(
            "leaderboard_opt_in", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "points_log",
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
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="daily"),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_points_log_user_day",
        "points_log",
        ["user_id", "occurred_on"],
        unique=False,
    )
    op.create_index(
        "ix_points_log_user_event_day",
        "points_log",
        ["user_id", "event_type", "occurred_on"],
        unique=False,
    )

    op.create_index(
        "ix_user_gamification_points_weekly",
        "user_gamification",
        ["points_weekly"],
        unique=False,
    )
    op.create_index(
        "ix_user_gamification_points_monthly",
        "user_gamification",
        ["points_monthly"],
        unique=False,
    )
    op.create_index(
        "ix_user_gamification_points_total",
        "user_gamification",
        ["points_total"],
        unique=False,
    )

    op.alter_column("user_gamification", "points_total", server_default=None)
    op.alter_column("user_gamification", "points_weekly", server_default=None)
    op.alter_column("user_gamification", "points_monthly", server_default=None)
    op.alter_column("user_gamification", "current_streak_days", server_default=None)
    op.alter_column("user_gamification", "longest_streak_days", server_default=None)
    op.alter_column("user_gamification", "freeze_tokens", server_default=None)
    op.alter_column("user_gamification", "leaderboard_opt_in", server_default=None)
    op.alter_column("points_log", "scope", server_default=None)
    op.alter_column("points_log", "points", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_user_gamification_points_total", table_name="user_gamification")
    op.drop_index("ix_user_gamification_points_monthly", table_name="user_gamification")
    op.drop_index("ix_user_gamification_points_weekly", table_name="user_gamification")
    op.drop_index("ix_points_log_user_event_day", table_name="points_log")
    op.drop_index("ix_points_log_user_day", table_name="points_log")
    op.drop_table("points_log")
    op.drop_table("user_gamification")
    op.drop_column("transactions", "source")
