"""allow positive envelope movements

Revision ID: 20240313_env_move_nonzero
Revises: 20240313_add_cash
Create Date: 2024-03-13 00:00:05.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20240313_env_move_nonzero"
down_revision = "20240313_add_cash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_env_move_amount_negative", "envelope_movements", type_="check"
    )
    op.create_check_constraint(
        "ck_env_move_amount_nonzero",
        "envelope_movements",
        "amount <> 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_env_move_amount_nonzero", "envelope_movements", type_="check"
    )
    op.create_check_constraint(
        "ck_env_move_amount_negative",
        "envelope_movements",
        "amount < 0",
    )
