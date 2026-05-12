"""backfill distribution allocation links and enforce sweep uniqueness

Revision ID: 20260501_distribution_backfill_and_sweep_uniqueness
Revises: 20260501_distribution_log_item_allocation_link
Create Date: 2026-05-01 12:25:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260501_distribution_backfill_and_sweep_uniqueness"
down_revision = "20260501_distribution_log_item_allocation_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep one row per natural sweep key before adding unique constraint.
    op.execute(
        """
        DELETE FROM sweeps s
        USING (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY user_id, from_envelope_period_id, to_envelope_period_id, amount, swept_on
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM sweeps
            ) ranked
            WHERE ranked.rn > 1
        ) d
        WHERE s.id = d.id
        """
    )

    op.create_unique_constraint(
        "uq_sweeps_user_from_to_amount_swept_on",
        "sweeps",
        [
            "user_id",
            "from_envelope_period_id",
            "to_envelope_period_id",
            "amount",
            "swept_on",
        ],
    )

    # Enforce one-to-one link when allocation_id is present.
    op.create_index(
        "uq_distribution_log_items_allocation_id_not_null",
        "distribution_log_items",
        ["allocation_id"],
        unique=True,
        postgresql_where=sa.text("allocation_id IS NOT NULL"),
    )

    # Best-effort injective backfill for legacy rows that predate allocation_id.
    # A given allocation_id is assigned to at most one log item.
    op.execute(
        """
        WITH candidates AS (
            SELECT
                dli.id AS item_id,
                ea.id AS allocation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY dli.id
                    ORDER BY ea.created_at DESC, ea.id DESC
                ) AS item_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY ea.id
                    ORDER BY dli.created_at ASC, dli.id ASC
                ) AS allocation_rank
            FROM distribution_log_items dli
            JOIN distribution_logs dl ON dl.id = dli.log_id
            JOIN envelope_allocations ea
              ON ea.user_id = dl.user_id
             AND ea.envelope_period_id = dli.to_envelope_period_id
             AND ea.amount = dli.amount
             AND ea.created_at >= dl.created_at
             AND ea.created_at <= dli.created_at
            WHERE dli.allocation_id IS NULL
        )
        UPDATE distribution_log_items dli
           SET allocation_id = c.allocation_id
          FROM candidates c
         WHERE dli.id = c.item_id
           AND c.item_rank = 1
           AND c.allocation_rank = 1
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_distribution_log_items_allocation_id_not_null",
        table_name="distribution_log_items",
    )
    op.drop_constraint(
        "uq_sweeps_user_from_to_amount_swept_on",
        "sweeps",
        type_="unique",
    )
