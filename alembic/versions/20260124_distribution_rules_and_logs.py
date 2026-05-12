"""add distribution rules and logs

Revision ID: 20260124_distribution_rules_and_logs
Revises: 20260122_goals
Create Date: 2026-01-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260124_distribution_rules_and_logs"
down_revision = "20260122_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auto_distribution_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("users", "auto_distribution_enabled", server_default=None)

    op.create_table(
        "distribution_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("percent", sa.Numeric(6, 3), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "auto_apply_on_income",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('goal','envelope')", name="ck_distribution_rules_target_type"
        ),
        sa.CheckConstraint(
            "mode IN ('fixed_per_period','percent_of_income')",
            name="ck_distribution_rules_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'fixed_per_period' AND amount IS NOT NULL AND percent IS NULL) OR "
            "(mode = 'percent_of_income' AND percent IS NOT NULL AND amount IS NULL)",
            name="ck_distribution_rules_amount_percent",
        ),
    )
    op.alter_column("distribution_rules", "priority", server_default=None)
    op.alter_column("distribution_rules", "enabled", server_default=None)
    op.alter_column("distribution_rules", "auto_apply_on_income", server_default=None)

    op.create_index(
        "ix_distribution_rules_user_priority",
        "distribution_rules",
        ["user_id", "priority", "created_at"],
    )

    op.create_table(
        "distribution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("trigger", sa.String(length=24), nullable=False),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("income_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("cash_before", sa.Numeric(12, 2), nullable=False),
        sa.Column("cash_after", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger IN ('income_auto','manual_apply')",
            name="ck_distribution_logs_trigger",
        ),
    )
    op.create_index(
        "ix_distribution_logs_user_created_at",
        "distribution_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_distribution_logs_income_auto_tx",
        "distribution_logs",
        ["user_id", "transaction_id"],
        unique=True,
        postgresql_where=sa.text(
            "trigger = 'income_auto' AND transaction_id IS NOT NULL"
        ),
    )

    op.create_table(
        "distribution_log_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "from_envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('goal','envelope')",
            name="ck_distribution_log_items_target_type",
        ),
    )
    op.create_index(
        "ix_distribution_log_items_log_id",
        "distribution_log_items",
        ["log_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_distribution_log_items_log_id", table_name="distribution_log_items")
    op.drop_table("distribution_log_items")

    op.drop_index(
        "uq_distribution_logs_income_auto_tx", table_name="distribution_logs"
    )
    op.drop_index(
        "ix_distribution_logs_user_created_at", table_name="distribution_logs"
    )
    op.drop_table("distribution_logs")

    op.drop_index(
        "ix_distribution_rules_user_priority", table_name="distribution_rules"
    )
    op.drop_table("distribution_rules")

    op.drop_column("users", "auto_distribution_enabled")

