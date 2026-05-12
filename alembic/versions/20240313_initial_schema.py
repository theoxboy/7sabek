"""initial schema

Revision ID: 20240313_initial
Revises:
Create Date: 2024-03-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240313_initial"
down_revision = None
branch_labels = None
depends_on = None


transaction_type_enum = postgresql.ENUM(
    "income",
    "expense",
    name="transaction_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    transaction_type_enum.create(bind, checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("sweep_interval_days", sa.Integer(), nullable=False),
        sa.Column("next_sweep_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
    )

    op.create_table(
        "envelopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "rollover_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_default_savings",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "deletable", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_envelopes_user_name"),
    )
    op.create_index(
        "uq_envelopes_user_default_savings",
        "envelopes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default_savings = true"),
    )

    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
    )

    op.create_table(
        "category_envelope_map",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "user_id", "category_id", name="uq_cat_env_map_user_category"
        ),
    )

    op.create_table(
        "envelope_periods",
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
        sa.Column(
            "opening_balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rollover_from_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "user_id",
            "envelope_id",
            "period_start",
            name="uq_env_period_user_env_start",
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_env_period_date_range"
        ),
    )

    op.create_table(
        "envelope_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.CheckConstraint("amount >= 0", name="ck_env_alloc_amount_non_negative"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column("type", transaction_type_enum, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
    )

    op.create_table(
        "envelope_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"),
            nullable=False,
        ),
        sa.Column(
            "envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "user_id", "transaction_id", name="uq_env_move_user_transaction"
        ),
        sa.CheckConstraint("amount < 0", name="ck_env_move_amount_negative"),
    )

    op.create_table(
        "sweeps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "from_envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id"),
            nullable=False,
        ),
        sa.Column(
            "to_envelope_period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelope_periods.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("swept_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.CheckConstraint("amount > 0", name="ck_sweeps_amount_positive"),
        sa.CheckConstraint(
            "from_envelope_period_id <> to_envelope_period_id",
            name="ck_sweeps_periods_distinct",
        ),
    )


def downgrade() -> None:
    op.drop_table("sweeps")
    op.drop_table("envelope_movements")
    op.drop_table("transactions")
    transaction_type_enum.drop(op.get_bind(), checkfirst=True)
    op.drop_table("envelope_allocations")
    op.drop_table("envelope_periods")
    op.drop_table("category_envelope_map")
    op.drop_table("categories")
    op.drop_index("uq_envelopes_user_default_savings", table_name="envelopes")
    op.drop_table("envelopes")
    op.drop_table("users")
