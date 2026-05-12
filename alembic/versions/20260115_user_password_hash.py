"""add password_hash to users

Revision ID: 20260115_user_password_hash
Revises: 20260115_alembicver_len
Create Date: 2026-01-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260115_user_password_hash"
down_revision = "20260115_alembicver_len"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
