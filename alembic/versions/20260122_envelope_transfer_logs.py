"""add envelope transfer logs

Revision ID: 20260122_envelope_transfer_logs
Revises: 20260117_env_move_tx_cascade
Create Date: 2026-01-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260122_envelope_transfer_logs"
down_revision = "20260117_env_move_tx_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "envelope_transfer_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "to_envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id"),
            nullable=False,
        ),
        sa.Column("from_envelope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_envelope_name", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("envelope_transfer_logs")
