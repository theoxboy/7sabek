"""guest funnel events (Mode Découverte analytics)

Revision ID: 20260903_guest_events
Revises: 20260902_guest_mode
Create Date: 2026-09-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260903_guest_events"
down_revision = "20260902_guest_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_guest_events_name_created", "guest_events", ["name", "created_at"], unique=False)
    op.create_index("ix_guest_events_user_id", "guest_events", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_guest_events_user_id", table_name="guest_events")
    op.drop_index("ix_guest_events_name_created", table_name="guest_events")
    op.drop_table("guest_events")
