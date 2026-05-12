"""add distribution items and runs

Revision ID: 20260126_distribution_simple
Revises: 20260124_distribution_rules_and_logs
Create Date: 2026-01-26 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260126_distribution_simple"
down_revision = "20260124_distribution_rules_and_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "distribution_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("fixed_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("fixed_priority", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Numeric(6, 2), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('envelope','goal')",
            name="ck_distribution_items_target_type",
        ),
        sa.CheckConstraint(
            "mode IN ('none','fixed','percent')",
            name="ck_distribution_items_mode",
        ),
        sa.UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            name="uq_distribution_items_target",
        ),
    )
    op.alter_column("distribution_items", "enabled", server_default=None)

    op.create_table(
        "distribution_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("income_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("cash_before", sa.Numeric(12, 2), nullable=False),
        sa.Column("cash_after", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger IN ('manual','income_auto')",
            name="ck_distribution_runs_trigger",
        ),
    )
    op.create_index(
        "ix_distribution_runs_user_created",
        "distribution_runs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_distribution_runs_tx",
        "distribution_runs",
        ["user_id", "transaction_id"],
        unique=True,
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
    )

    op.create_table(
        "distribution_run_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('envelope','goal')",
            name="ck_distribution_run_items_target_type",
        ),
    )
    op.create_index(
        "ix_distribution_run_items_run_id",
        "distribution_run_items",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_distribution_run_items_run_id", table_name="distribution_run_items")
    op.drop_table("distribution_run_items")

    op.drop_index("uq_distribution_runs_tx", table_name="distribution_runs")
    op.drop_index("ix_distribution_runs_user_created", table_name="distribution_runs")
    op.drop_table("distribution_runs")

    op.drop_table("distribution_items")

