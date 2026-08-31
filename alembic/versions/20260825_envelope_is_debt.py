"""explicit is_debt flag on envelopes

Revision ID: 20260825_env_is_debt
Revises: 20260825_auto_sweep_err
Create Date: 2026-08-25 12:00:00.000000

Debt envelopes had no dedicated flag — "debt" was inferred live from a keyword
match on the envelope name. A name that missed the keywords (typo, "Salaf",
"Kredit") was left sweep-eligible and its repayment fund could be swept into
savings; a name that hit them by accident ("Carte de crédit") was force-locked
to rollover-on. This adds an explicit column and backfills it once from the
(expanded) heuristic; the sweep and the rollover guard read the column from
here on.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_env_is_debt"
down_revision: Union[str, None] = "20260825_auto_sweep_err"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept in the migration so the backfill is frozen regardless of later code edits.
_DEBT_KEYWORDS = (
    "dette", "dettes", "debt", "debts", "credit", "crédit", "crédits",
    "kredit", "كريدي", "repayment", "repayments", "loan", "loans",
    "salaf", "سلف", "دين", "الديون", "ديون", "قرض", "قروض",
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("envelopes")}
    if "is_debt" not in cols:
        op.add_column(
            "envelopes",
            sa.Column("is_debt", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    # One-time backfill from the name heuristic. lower() is enough here: the
    # keywords are already lowercase and Arabic is case-insensitive.
    like_clauses = " OR ".join("lower(name) LIKE :kw%d" % i for i in range(len(_DEBT_KEYWORDS)))
    params = {"kw%d" % i: "%%%s%%" % kw for i, kw in enumerate(_DEBT_KEYWORDS)}
    conn.execute(
        sa.text(
            "UPDATE envelopes SET is_debt = true "
            "WHERE is_debt = false AND is_goal = false AND is_cash = false "
            "AND is_default_savings = false AND (" + like_clauses + ")"
        ),
        params,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("envelopes")}
    if "is_debt" in cols:
        op.drop_column("envelopes", "is_debt")
