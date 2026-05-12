"""add user shiftpilot states

Revision ID: 20260309_user_shiftpilot_states
Revises: 20260307_onboarding_v2_records
Create Date: 2026-03-09 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260309_user_shiftpilot_states"
down_revision = "20260307_onboarding_v2_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_shiftpilot_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_shiftpilot_states_user_id"),
    )
    op.create_index(
        "ix_user_shiftpilot_states_user_id",
        "user_shiftpilot_states",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_shiftpilot_states_user_id", table_name="user_shiftpilot_states")
    op.drop_table("user_shiftpilot_states")
