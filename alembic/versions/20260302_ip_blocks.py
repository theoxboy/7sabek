"""add ip blocks

Revision ID: 20260302_ip_blocks
Revises: 20260301_superadmin_sessions
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260302_ip_blocks"
down_revision = "20260301_superadmin_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ip_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False, unique=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "blocked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("superadmin_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ip_blocks_ip_address",
        "ip_blocks",
        ["ip_address"],
        unique=True,
    )
    op.create_index(
        "ix_ip_blocks_created_at",
        "ip_blocks",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ip_blocks_created_at", table_name="ip_blocks")
    op.drop_index("ix_ip_blocks_ip_address", table_name="ip_blocks")
    op.drop_table("ip_blocks")

