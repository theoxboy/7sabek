"""add envelope adjustment logs

Revision ID: 20260122_envelope_adjustment_logs
Revises: 20260122_envelope_transfer_logs
Create Date: 2026-01-22 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260122_envelope_adjustment_logs"
down_revision = "20260122_envelope_transfer_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "envelope_adjustment_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("previous_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("delta", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("envelope_adjustment_logs")
