"""one income distribution per transaction

Revision ID: 20260826_income_dist_once
Revises: 20260825_env_is_debt
Create Date: 2026-08-26 09:00:00.000000

Income distribution used to have two entry points — the backend inside
POST /transactions (when auto_distribution_enabled) and the frontend via
POST /distribution/apply (when not). A stale client/server flag could run both
(double distribution) or neither (silent gap). apply_distribution_plan already
no-ops on a duplicate income_auto log for the same transaction, but nothing
stopped two concurrent inserts. This partial unique index makes "one income_auto
distribution per (user, transaction)" a hard database guarantee, so the two
callers can both fire safely and the second is rejected, not applied.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_income_dist_once"
down_revision: Union[str, None] = "20260825_env_is_debt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_distribution_logs_income_auto_txn"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {ix["name"] for ix in inspector.get_indexes("distribution_logs")}
    if _INDEX in existing:
        return

    # Collapse any pre-existing duplicates to the earliest row before the index
    # goes on (there should be none, but be safe).
    conn.execute(
        sa.text(
            """
            DELETE FROM distribution_logs dl
            USING distribution_logs keep
            WHERE dl.trigger = 'income_auto'
              AND dl.transaction_id IS NOT NULL
              AND keep.trigger = 'income_auto'
              AND keep.transaction_id = dl.transaction_id
              AND keep.user_id = dl.user_id
              AND keep.created_at < dl.created_at
            """
        )
    )

    op.create_index(
        _INDEX,
        "distribution_logs",
        ["user_id", "transaction_id"],
        unique=True,
        postgresql_where=sa.text(
            "trigger = 'income_auto' AND transaction_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {ix["name"] for ix in inspector.get_indexes("distribution_logs")}
    if _INDEX in existing:
        op.drop_index(_INDEX, table_name="distribution_logs")
