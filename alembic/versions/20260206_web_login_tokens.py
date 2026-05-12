"""add web login tokens

Revision ID: 20260206_web_login_tokens
Revises: 20260205_income_movements_backfill
Create Date: 2026-02-06 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260206_web_login_tokens"
down_revision = "20260205_income_movements_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_login_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_web_login_tokens_token",
        "web_login_tokens",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_web_login_tokens_token", table_name="web_login_tokens")
    op.drop_table("web_login_tokens")
