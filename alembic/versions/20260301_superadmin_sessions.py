"""add superadmin sessions

Revision ID: 20260301_superadmin_sessions
Revises: 20260228_leaderboard_name_changes
Create Date: 2026-03-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260301_superadmin_sessions"
down_revision = "20260228_leaderboard_name_changes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "superadmin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("browser", sa.String(length=120), nullable=True),
        sa.Column("os", sa.String(length=120), nullable=True),
        sa.Column("device", sa.String(length=80), nullable=True),
        sa.Column("geo_lat", sa.Float(), nullable=True),
        sa.Column("geo_lng", sa.Float(), nullable=True),
        sa.Column("geo_accuracy_m", sa.Float(), nullable=True),
        sa.Column("geo_label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_superadmin_sessions_user_active",
        "superadmin_sessions",
        ["user_id", "revoked_at", "ended_at"],
        unique=False,
    )
    op.create_index(
        "ix_superadmin_sessions_last_seen_at",
        "superadmin_sessions",
        ["last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_superadmin_sessions_session_token_hash",
        "superadmin_sessions",
        ["session_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_superadmin_sessions_session_token_hash",
        table_name="superadmin_sessions",
    )
    op.drop_index("ix_superadmin_sessions_last_seen_at", table_name="superadmin_sessions")
    op.drop_index("ix_superadmin_sessions_user_active", table_name="superadmin_sessions")
    op.drop_table("superadmin_sessions")

