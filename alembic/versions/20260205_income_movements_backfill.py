"""backfill income movements into cash

Revision ID: 20260205_income_movements_backfill
Revises: 20260202_income_reminders
Create Date: 2026-02-05 00:00:00.000000
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260205_income_movements_backfill"
down_revision = "20260202_income_reminders"
branch_labels = None
depends_on = None


def _period_bounds(anchor: date, occurred_on: date, interval_days: int) -> tuple[date, date]:
    if interval_days <= 0:
        raise ValueError("interval_days must be positive")
    delta_days = (occurred_on - anchor).days
    bucket = delta_days // interval_days
    period_start = anchor + timedelta(days=bucket * interval_days)
    period_end = period_start + timedelta(days=interval_days)
    return period_start, period_end


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or 0))


def upgrade() -> None:
    conn = op.get_bind()

    users = sa.table(
        "users",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("sweep_interval_days", sa.Integer),
    )
    envelopes = sa.table(
        "envelopes",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("rollover_enabled", sa.Boolean),
        sa.column("is_default_savings", sa.Boolean),
        sa.column("is_cash", sa.Boolean),
        sa.column("is_goal", sa.Boolean),
        sa.column("deletable", sa.Boolean),
    )
    transactions = sa.table(
        "transactions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("type", sa.String),
        sa.column("amount", sa.Numeric(12, 2)),
        sa.column("occurred_on", sa.Date),
    )
    envelope_movements = sa.table(
        "envelope_movements",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("transaction_id", postgresql.UUID(as_uuid=True)),
        sa.column("envelope_period_id", postgresql.UUID(as_uuid=True)),
        sa.column("amount", sa.Numeric(12, 2)),
    )
    envelope_periods = sa.table(
        "envelope_periods",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("envelope_id", postgresql.UUID(as_uuid=True)),
        sa.column("period_start", sa.Date),
        sa.column("period_end", sa.Date),
        sa.column("opening_balance", sa.Numeric(12, 2)),
        sa.column("rollover_from_period_id", postgresql.UUID(as_uuid=True)),
    )
    envelope_allocations = sa.table(
        "envelope_allocations",
        sa.column("envelope_period_id", postgresql.UUID(as_uuid=True)),
        sa.column("amount", sa.Numeric(12, 2)),
    )
    sweeps = sa.table(
        "sweeps",
        sa.column("from_envelope_period_id", postgresql.UUID(as_uuid=True)),
        sa.column("to_envelope_period_id", postgresql.UUID(as_uuid=True)),
        sa.column("amount", sa.Numeric(12, 2)),
    )

    income_rows = conn.execute(
        sa.select(
            transactions.c.id,
            transactions.c.user_id,
            transactions.c.amount,
            transactions.c.occurred_on,
            users.c.created_at,
            users.c.sweep_interval_days,
        )
        .select_from(
            transactions.join(users, users.c.id == transactions.c.user_id)
            .outerjoin(
                envelope_movements,
                envelope_movements.c.transaction_id == transactions.c.id,
            )
        )
        .where(
            sa.text("transactions.type = 'income'::transaction_type"),
            envelope_movements.c.id.is_(None),
        )
    ).fetchall()

    cash_envelope_cache: dict[UUID, UUID] = {}
    period_cache: dict[tuple[UUID, UUID, date, date], UUID] = {}

    for row in income_rows:
        user_id = row.user_id
        occurred_on = row.occurred_on
        anchor_date = row.created_at.date()
        interval_days = int(row.sweep_interval_days)

        cash_id = cash_envelope_cache.get(user_id)
        if not cash_id:
            cash_id = conn.execute(
                sa.select(envelopes.c.id).where(
                    envelopes.c.user_id == user_id,
                    envelopes.c.is_cash.is_(True),
                )
            ).scalar()
            if not cash_id:
                cash_id = uuid4()
                conn.execute(
                    envelopes.insert().values(
                        id=cash_id,
                        user_id=user_id,
                        name="Cash",
                        rollover_enabled=False,
                        is_default_savings=False,
                        is_cash=True,
                        is_goal=False,
                        deletable=False,
                    )
                )
            cash_envelope_cache[user_id] = cash_id

        period_start, period_end = _period_bounds(anchor_date, occurred_on, interval_days)
        period_key = (user_id, cash_id, period_start, period_end)
        period_id = period_cache.get(period_key)

        if not period_id:
            period_id = conn.execute(
                sa.select(envelope_periods.c.id).where(
                    envelope_periods.c.user_id == user_id,
                    envelope_periods.c.envelope_id == cash_id,
                    envelope_periods.c.period_start == period_start,
                    envelope_periods.c.period_end == period_end,
                )
            ).scalar()

            if not period_id:
                prev_period = conn.execute(
                    sa.select(
                        envelope_periods.c.id,
                        envelope_periods.c.opening_balance,
                    )
                    .where(
                        envelope_periods.c.user_id == user_id,
                        envelope_periods.c.envelope_id == cash_id,
                        envelope_periods.c.period_end <= period_start,
                    )
                    .order_by(envelope_periods.c.period_end.desc())
                    .limit(1)
                ).first()

                opening_balance = Decimal("0.00")
                rollover_from_period_id: Optional[UUID] = None

                if prev_period:
                    prev_period_id = prev_period.id
                    opening_balance = _decimal(prev_period.opening_balance)
                    allocations = _decimal(
                        conn.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.sum(envelope_allocations.c.amount), 0)
                            ).where(envelope_allocations.c.envelope_period_id == prev_period_id)
                        ).scalar()
                    )
                    movements = _decimal(
                        conn.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.sum(envelope_movements.c.amount), 0)
                            ).where(envelope_movements.c.envelope_period_id == prev_period_id)
                        ).scalar()
                    )
                    sweeps_out = _decimal(
                        conn.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.sum(sweeps.c.amount), 0)
                            ).where(sweeps.c.from_envelope_period_id == prev_period_id)
                        ).scalar()
                    )
                    sweeps_in = _decimal(
                        conn.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.sum(sweeps.c.amount), 0)
                            ).where(sweeps.c.to_envelope_period_id == prev_period_id)
                        ).scalar()
                    )
                    opening_balance = (
                        opening_balance + allocations + movements - sweeps_out + sweeps_in
                    )
                    rollover_from_period_id = prev_period_id

                period_id = uuid4()
                conn.execute(
                    envelope_periods.insert().values(
                        id=period_id,
                        user_id=user_id,
                        envelope_id=cash_id,
                        period_start=period_start,
                        period_end=period_end,
                        opening_balance=opening_balance,
                        rollover_from_period_id=rollover_from_period_id,
                    )
                )

            period_cache[period_key] = period_id

        conn.execute(
            envelope_movements.insert().values(
                id=uuid4(),
                user_id=user_id,
                transaction_id=row.id,
                envelope_period_id=period_id,
                amount=row.amount,
            )
        )


def downgrade() -> None:
    pass
