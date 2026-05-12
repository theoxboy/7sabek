"""link distribution log items to envelope allocations

Revision ID: 20260501_distribution_log_item_allocation_link
Revises: 20260427_user_password_reset_block_policy
Create Date: 2026-05-01 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260501_distribution_log_item_allocation_link"
down_revision = "20260427_user_password_reset_block_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "distribution_log_items",
        sa.Column("allocation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_distribution_log_items_allocation_id",
        "distribution_log_items",
        "envelope_allocations",
        ["allocation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_distribution_log_items_allocation_id",
        "distribution_log_items",
        type_="foreignkey",
    )
    op.drop_column("distribution_log_items", "allocation_id")

