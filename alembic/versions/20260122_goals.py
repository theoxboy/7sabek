"""add goals and goal envelopes

Revision ID: 20260122_goals
Revises: 20260122_envelope_adjustment_logs
Create Date: 2026-01-22 08:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260122_goals"
down_revision = "20260122_envelope_adjustment_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "envelopes",
        sa.Column("is_goal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.alter_column("envelopes", "is_goal", server_default=None)

    op.create_table(
        "goals",
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
            sa.ForeignKey("envelopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("target_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("contribution_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("auto_contribute", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.alter_column("goals", "auto_contribute", server_default=None)
    op.alter_column("goals", "priority", server_default=None)


def downgrade() -> None:
    op.drop_table("goals")
    op.drop_column("envelopes", "is_goal")
