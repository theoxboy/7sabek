"""add page views

Revision ID: 20260209_add_page_views
Revises: 20260208_add_user_role
Create Date: 2026-02-09 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260209_add_page_views"
down_revision = "20260208_add_user_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_views",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="direct",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_page_views_user_id", "page_views", ["user_id"])
    op.create_index("ix_page_views_created_at", "page_views", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_page_views_created_at", table_name="page_views")
    op.drop_index("ix_page_views_user_id", table_name="page_views")
    op.drop_table("page_views")
