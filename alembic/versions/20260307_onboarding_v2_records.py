"""add onboarding v2 records table

Revision ID: 20260307_onboarding_v2_records
Revises: 20260304_user_beta_tester_flag
Create Date: 2026-03-07 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260307_onboarding_v2_records"
down_revision = "20260304_user_beta_tester_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_v2_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "flow_version",
            sa.String(length=32),
            nullable=False,
            server_default="v2",
        ),
        sa.Column(
            "stage",
            sa.String(length=20),
            nullable=False,
            server_default="completed",
        ),
        sa.Column("income_type", sa.String(length=40), nullable=True),
        sa.Column("primary_objective", sa.String(length=80), nullable=True),
        sa.Column("household_type", sa.String(length=80), nullable=True),
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
    )
    op.create_index(
        "ix_onboarding_v2_records_user_id",
        "onboarding_v2_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_onboarding_v2_records_created_at",
        "onboarding_v2_records",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_v2_records_created_at", table_name="onboarding_v2_records")
    op.drop_index("ix_onboarding_v2_records_user_id", table_name="onboarding_v2_records")
    op.drop_table("onboarding_v2_records")
