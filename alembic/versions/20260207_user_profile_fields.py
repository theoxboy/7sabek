"""add user profile fields

Revision ID: 20260207_user_profile_fields
Revises: 20260206_web_login_tokens
Create Date: 2026-02-07 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260207_user_profile_fields"
down_revision = "20260206_web_login_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("phone_number", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("profile_photo_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "profile_photo_url")
    op.drop_column("users", "city")
    op.drop_column("users", "country")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "phone_number")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
