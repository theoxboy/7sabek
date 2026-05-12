"""add per-user password reset block policy

Revision ID: 20260427_user_password_reset_block_policy
Revises: 20260426_password_reset_tokens
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_user_password_reset_block_policy"
down_revision = "20260426_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_reset_block_mode",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_blocked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_block_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_reset_blocked_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_users_password_reset_blocked_by_user_id",
        "users",
        "users",
        ["password_reset_blocked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_users_password_reset_blocked_by_user_id",
        "users",
        type_="foreignkey",
    )
    op.drop_column("users", "password_reset_blocked_by_user_id")
    op.drop_column("users", "password_reset_blocked_at")
    op.drop_column("users", "password_reset_block_reason")
    op.drop_column("users", "password_reset_blocked_until")
    op.drop_column("users", "password_reset_block_mode")
