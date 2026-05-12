from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnvelopeAllocation, EnvelopeMovement, EnvelopePeriod, Sweep


async def compute_period_balance(
    db: AsyncSession, period_id: UUID
) -> dict[str, Decimal]:
    period_result = await db.execute(
        select(EnvelopePeriod).where(EnvelopePeriod.id == period_id)
    )
    period = period_result.scalar_one()

    allocations_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeAllocation.amount), 0)).where(
            EnvelopeAllocation.envelope_period_id == period_id
        )
    )
    total_allocations = Decimal(str(allocations_result.scalar_one()))

    movements_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), 0)).where(
            EnvelopeMovement.envelope_period_id == period_id
        )
    )
    total_movements = Decimal(str(movements_result.scalar_one()))

    sweeps_out_result = await db.execute(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.from_envelope_period_id == period_id
        )
    )
    sweeps_out = Decimal(str(sweeps_out_result.scalar_one()))

    sweeps_in_result = await db.execute(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.to_envelope_period_id == period_id
        )
    )
    sweeps_in = Decimal(str(sweeps_in_result.scalar_one()))

    opening_balance = Decimal(str(period.opening_balance))
    closing_balance = (
        opening_balance + total_allocations + total_movements - sweeps_out + sweeps_in
    )
    total_spent = -total_movements if total_movements < 0 else Decimal("0")

    return {
        "opening_balance": opening_balance,
        "total_allocations": total_allocations,
        "total_spent": total_spent,
        "closing_balance": closing_balance,
    }
