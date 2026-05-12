from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DistributionItem,
    DistributionRun,
    DistributionRunItem,
    Envelope,
    EnvelopeAllocation,
    EnvelopeMovement,
    Goal,
    User,
)
from app.services.balances import compute_period_balance
from app.services.sweep_context import resolve_user_sweep_anchor_date


@dataclass(frozen=True)
class DistributionContext:
    occurred_on: date
    period_start: date
    period_end: date


@dataclass(frozen=True)
class DistributionPlanItem:
    target_type: str
    target_id: UUID
    target_name: str
    mode: str
    amount: Decimal
    priority: Optional[int] = None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _parse_decimal(value: Optional[Decimal]) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0.00")


async def _cash_period_id(db: AsyncSession, user: User, occurred_on: date) -> UUID:
    from app.services.transactions import get_or_create_envelope_period, resolve_cash_envelope

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
    return cash_period.id


async def cash_available_for_period(
    db: AsyncSession, user: User, occurred_on: date
) -> Decimal:
    cash_period_id = await _cash_period_id(db, user, occurred_on)
    balance = await compute_period_balance(db, cash_period_id)
    return balance["closing_balance"]


def _fixed_items(items: Iterable[DistributionItem]) -> list[DistributionItem]:
    return sorted(
        (item for item in items if item.enabled and item.mode == "fixed"),
        key=lambda item: item.fixed_priority or 10_000,
    )


def _percent_items(items: Iterable[DistributionItem]) -> list[DistributionItem]:
    return [item for item in items if item.enabled and item.mode == "percent"]


async def _target_snapshot(
    db: AsyncSession, user: User, target_type: str, target_id: UUID
) -> tuple[str, UUID]:
    if target_type == "envelope":
        result = await db.execute(
            select(Envelope).where(
                Envelope.user_id == user.id, Envelope.id == target_id
            )
        )
        envelope = result.scalar_one_or_none()
        if envelope is None:
            raise ValueError("Envelope not found")
        return envelope.name, envelope.id

    result = await db.execute(
        select(Goal).where(Goal.user_id == user.id, Goal.id == target_id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        raise ValueError("Goal not found")
    return goal.name, goal.envelope_id


async def build_plan(
    db: AsyncSession,
    user: User,
    ctx: DistributionContext,
    items: Iterable[DistributionItem],
    cash_available: Decimal,
    income_amount: Optional[Decimal],
    use_cash_available: bool,
) -> tuple[list[DistributionPlanItem], list[str]]:
    warnings: list[str] = []
    plan: list[DistributionPlanItem] = []
    remaining = cash_available

    for item in _fixed_items(items):
        amount = _parse_decimal(item.fixed_amount)
        if amount <= 0:
            continue
        applied = min(amount, remaining)
        if applied < amount:
            warnings.append(
                f"Fixe partiel: {item.target_type}:{item.target_id} ({applied}/{amount})"
            )
        if applied > 0:
            name, _ = await _target_snapshot(db, user, item.target_type, item.target_id)
            plan.append(
                DistributionPlanItem(
                    target_type=item.target_type,
                    target_id=item.target_id,
                    target_name=name,
                    mode="fixed",
                    amount=_quantize(applied),
                    priority=item.fixed_priority,
                )
            )
            remaining = _quantize(remaining - applied)
        if remaining <= 0:
            break

    percent_items = _percent_items(items)
    if remaining > 0 and percent_items:
        total_percent = Decimal("0.00")
        valid_items: list[DistributionItem] = []
        for item in percent_items:
            percent = _parse_decimal(item.percent)
            if percent <= 0:
                continue
            total_percent += percent
            valid_items.append(item)

        if total_percent > 0:
            running = Decimal("0.00")
            for index, item in enumerate(valid_items):
                percent = _parse_decimal(item.percent)
                if index == len(valid_items) - 1:
                    applied = _quantize(remaining - running)
                    if applied < 0:
                        applied = Decimal("0.00")
                else:
                    applied = _quantize(remaining * (percent / total_percent))
                    running = _quantize(running + applied)

                if applied <= 0:
                    continue
                name, _ = await _target_snapshot(
                    db, user, item.target_type, item.target_id
                )
                plan.append(
                    DistributionPlanItem(
                        target_type=item.target_type,
                        target_id=item.target_id,
                        target_name=name,
                        mode="percent",
                        amount=applied,
                    )
                )

    return plan, warnings


async def apply_plan(
    db: AsyncSession,
    user: User,
    ctx: DistributionContext,
    plan: list[DistributionPlanItem],
    trigger: str,
    transaction_id: Optional[UUID],
    income_amount: Optional[Decimal],
    cash_before: Decimal,
) -> DistributionRun:
    if trigger not in {"manual", "income_auto"}:
        raise ValueError("Invalid trigger")

    if trigger == "income_auto" and transaction_id is not None:
        existing = await db.execute(
            select(DistributionRun).where(
                DistributionRun.user_id == user.id,
                DistributionRun.transaction_id == transaction_id,
            )
        )
        run = existing.scalar_one_or_none()
        if run is not None:
            return run

    cash_period_id = await _cash_period_id(db, user, ctx.occurred_on)
    total_distributed = sum((item.amount for item in plan), Decimal("0.00"))
    cash_after = _quantize(cash_before - total_distributed)

    anchor_date = await resolve_user_sweep_anchor_date(db, user)

    async with db.begin_nested():
        run = DistributionRun(
            user_id=user.id,
            trigger=trigger,
            period_start=ctx.period_start,
            period_end=ctx.period_end,
            income_amount=income_amount,
            cash_before=cash_before,
            cash_after=cash_after,
            transaction_id=transaction_id,
        )
        db.add(run)
        await db.flush()

        from app.services.transactions import get_or_create_envelope_period

        for item in plan:
            _, target_envelope_id = await _target_snapshot(
                db, user, item.target_type, item.target_id
            )

            target_period = await get_or_create_envelope_period(
                db,
                user.id,
                target_envelope_id,
                ctx.occurred_on,
                user.sweep_interval_days,
                anchor_date,
            )
            db.add(
                EnvelopeAllocation(
                    user_id=user.id,
                    envelope_period_id=target_period.id,
                    amount=item.amount,
                )
            )
            db.add(
                EnvelopeMovement(
                    user_id=user.id,
                    transaction_id=None,
                    envelope_period_id=cash_period_id,
                    amount=-item.amount,
                )
            )
            db.add(
                DistributionRunItem(
                    run_id=run.id,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    name_snapshot=item.target_name,
                    mode=item.mode,
                    amount=item.amount,
                )
            )

    return run
