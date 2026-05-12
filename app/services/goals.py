from __future__ import annotations

from datetime import date
from decimal import Decimal
from math import ceil
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Envelope,
    EnvelopeAllocation,
    EnvelopeMovement,
    EnvelopePeriod,
    Goal,
    Sweep,
    User,
)
from app.services.sweep_context import resolve_user_sweep_anchor_date


def compute_contribution_amount(
    target_amount: Decimal,
    target_date: Optional[date],
    sweep_interval_days: int,
) -> Decimal:
    if not target_date:
        return target_amount
    days_left = max((target_date - date.today()).days, sweep_interval_days)
    periods_left = max(1, ceil(days_left / sweep_interval_days))
    return (target_amount / Decimal(periods_left)).quantize(Decimal("0.01"))


async def get_envelope_total_balance(
    db: AsyncSession, user_id: UUID, envelope_id: UUID
) -> Decimal:
    period_ids = select(EnvelopePeriod.id).where(
        EnvelopePeriod.user_id == user_id,
        EnvelopePeriod.envelope_id == envelope_id,
    )

    allocations_sum = await db.scalar(
        select(func.coalesce(func.sum(EnvelopeAllocation.amount), 0)).where(
            EnvelopeAllocation.envelope_period_id.in_(period_ids)
        )
    )
    movements_sum = await db.scalar(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), 0)).where(
            EnvelopeMovement.envelope_period_id.in_(period_ids)
        )
    )
    sweeps_out_sum = await db.scalar(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.from_envelope_period_id.in_(period_ids)
        )
    )
    sweeps_in_sum = await db.scalar(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.to_envelope_period_id.in_(period_ids)
        )
    )

    return (
        Decimal(str(allocations_sum))
        + Decimal(str(movements_sum))
        - Decimal(str(sweeps_out_sum))
        + Decimal(str(sweeps_in_sum))
    )


async def get_period_allocations_total(
    db: AsyncSession, period_id: UUID
) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(EnvelopeAllocation.amount), 0)).where(
            EnvelopeAllocation.envelope_period_id == period_id
        )
    )
    return Decimal(str(total))


async def allocate_from_cash(
    db: AsyncSession,
    user: User,
    target_envelope_id: UUID,
    amount: Decimal,
    occurred_on: date,
) -> None:
    if amount <= 0:
        return
    from app.services.transactions import (
        get_or_create_envelope_period,
        resolve_cash_envelope,
    )

    anchor_date = await resolve_user_sweep_anchor_date(db, user)
    cash_envelope = await resolve_cash_envelope(db, user.id)
    cash_period = await get_or_create_envelope_period(
        db,
        user.id,
        cash_envelope.id,
        occurred_on,
        user.sweep_interval_days,
        anchor_date,
    )
    target_period = await get_or_create_envelope_period(
        db,
        user.id,
        target_envelope_id,
        occurred_on,
        user.sweep_interval_days,
        anchor_date,
    )
    db.add(
        EnvelopeAllocation(
            user_id=user.id,
            envelope_period_id=target_period.id,
            amount=amount,
        )
    )
    db.add(
        EnvelopeMovement(
            user_id=user.id,
            transaction_id=None,
            envelope_period_id=cash_period.id,
            amount=-amount,
        )
    )


async def distribute_income_to_goals(
    db: AsyncSession,
    user: User,
    goals: Iterable[Goal],
    income_amount: Decimal,
    occurred_on: date,
) -> None:
    if income_amount <= 0:
        return

    targets: list[tuple[Goal, Decimal]] = []
    anchor_date = await resolve_user_sweep_anchor_date(db, user)
    for goal in goals:
        total_balance = await get_envelope_total_balance(
            db, user.id, goal.envelope_id
        )
        remaining_to_target = goal.target_amount - total_balance
        if remaining_to_target <= 0:
            continue

        period = await get_or_create_envelope_period(
            db,
            user.id,
            goal.envelope_id,
            occurred_on,
            user.sweep_interval_days,
            anchor_date,
        )
        already_allocated = await get_period_allocations_total(db, period.id)
        remaining_for_period = goal.contribution_amount - already_allocated
        if remaining_for_period <= 0:
            continue

        target_amount = min(remaining_for_period, remaining_to_target)
        if target_amount > 0:
            targets.append((goal, target_amount))

    if not targets:
        return

    total_target = sum(amount for _, amount in targets)
    if total_target <= 0:
        return

    scale = (
        Decimal("1.0")
        if total_target <= income_amount
        else (income_amount / total_target)
    )
    for goal, target_amount in targets:
        allocation_amount = (target_amount * scale).quantize(Decimal("0.01"))
        await allocate_from_cash(
            db,
            user,
            goal.envelope_id,
            allocation_amount,
            occurred_on,
        )
