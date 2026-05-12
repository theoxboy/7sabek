"""backup records

Revision ID: 20260224_backup_records
Revises: 20260223_admin_activity_actor_ip
Create Date: 2026-02-24 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260224_backup_records"
down_revision = "20260223_admin_activity_actor_ip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("message", sa.String(length=400), nullable=True),
    )
    op.create_index("ix_backup_records_id", "backup_records", ["id"], unique=False)
    op.create_index(
        "ix_backup_records_created_at",
        "backup_records",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_backup_records_created_at", table_name="backup_records")
    op.drop_index("ix_backup_records_id", table_name="backup_records")
    op.drop_table("backup_records")
