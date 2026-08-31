"""track auto-sweep failures on the user

Revision ID: 20260825_auto_sweep_err
Revises: 20260824_debts_salaf
Create Date: 2026-08-25 10:00:00.000000

Auto-sweep runs opportunistically on login / transaction create inside a
best-effort try/except, so a failure was previously invisible: the period never
closes, the "sweep due" nag never clears, and distribution can get locked, with
no signal to the user. These two columns record the last failed attempt so the
dashboard can surface it; a later successful auto-sweep clears them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_auto_sweep_err"
down_revision: Union[str, None] = "20260824_debts_salaf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {col["name"] for col in inspector.get_columns("users")}
    if "last_auto_sweep_error_at" not in existing:
        op.add_column(
            "users",
            sa.Column("last_auto_sweep_error_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "last_auto_sweep_error" not in existing:
        op.add_column(
            "users",
            sa.Column("last_auto_sweep_error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {col["name"] for col in inspector.get_columns("users")}
    if "last_auto_sweep_error" in existing:
        op.drop_column("users", "last_auto_sweep_error")
    if "last_auto_sweep_error_at" in existing:
        op.drop_column("users", "last_auto_sweep_error_at")
