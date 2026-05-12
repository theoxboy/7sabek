"""user soft delete grace period

Revision ID: 20260225_user_soft_delete_grace
Revises: 20260224_backup_records
Create Date: 2026-02-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260225_user_soft_delete_grace"
down_revision = "20260224_backup_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "account_deletion_grace_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.alter_column(
        "platform_settings", "account_deletion_grace_days", server_default=None
    )


def downgrade() -> None:
    op.drop_column("platform_settings", "account_deletion_grace_days")
    op.drop_column("users", "deleted_at")
