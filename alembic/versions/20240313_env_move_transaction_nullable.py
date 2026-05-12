"""allow envelope movement without transaction

Revision ID: 20240313_env_move_tx_nullable
Revises: 20240313_env_move_nonzero
Create Date: 2024-03-13 00:00:06.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20240313_env_move_tx_nullable"
down_revision = "20240313_env_move_nonzero"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("envelope_movements", "transaction_id", nullable=True)


def downgrade() -> None:
    op.alter_column("envelope_movements", "transaction_id", nullable=False)
