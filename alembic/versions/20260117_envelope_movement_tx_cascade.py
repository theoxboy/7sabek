"""Add cascade delete for envelope_movements.transaction_id.

Revision ID: 20260117_env_move_tx_cascade
Revises: 20260115_user_password_hash
Create Date: 2026-01-17 21:15:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260117_env_move_tx_cascade"
down_revision = "20260115_user_password_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "envelope_movements_transaction_id_fkey",
        "envelope_movements",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "envelope_movements_transaction_id_fkey",
        "envelope_movements",
        "transactions",
        ["transaction_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "envelope_movements_transaction_id_fkey",
        "envelope_movements",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "envelope_movements_transaction_id_fkey",
        "envelope_movements",
        "transactions",
        ["transaction_id"],
        ["id"],
    )
