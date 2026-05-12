from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DistributionLog,
    DistributionLogItem,
    DistributionRule,
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
    rule_id: UUID
    target_type: str  # 'goal' | 'envelope'
    target_id: UUID
    target_name: str
    to_envelope_id: UUID
    amount: Decimal


def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


async def _cash_period_id(
    db: AsyncSession, user: User, occurred_on: date
) -> UUID:
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


def _fixed_rules(rules: Iterable[DistributionRule]) -> list[DistributionRule]:
    return sorted(
        (rule for rule in rules if rule.enabled and rule.mode == "fixed_per_period"),
        key=lambda rule: rule.rank,
    )


def _percent_rules(rules: Iterable[DistributionRule]) -> list[DistributionRule]:
    return sorted(
        (
            rule
            for rule in rules
            if rule.enabled and rule.mode == "percent_of_income"
        ),
        key=lambda rule: rule.rank,
    )


async def build_distribution_plan(
    db: AsyncSession,
    user: User,
    ctx: DistributionContext,
    rules: Iterable[DistributionRule],
    cash_available: Decimal,
    base_amount: Decimal,
    apply_income_filter: bool = False,
) -> list[DistributionPlanItem]:
    if cash_available <= 0 or base_amount <= 0:
        return []

    plan: list[DistributionPlanItem] = []
    cash_remaining = min(cash_available, base_amount)

    for rule in _fixed_rules(rules):
        if apply_income_filter and not rule.auto_apply_on_income:
            continue

        amount = Decimal(str(rule.amount or 0))
        if amount <= 0:
            continue
        desired_amount = min(amount, cash_remaining)
        if desired_amount <= 0:
            break

        if rule.target_type == "envelope":
            envelope_result = await db.execute(
                select(Envelope).where(
                    Envelope.user_id == user.id,
                    Envelope.id == rule.target_id,
                )
            )
            envelope = envelope_result.scalar_one_or_none()
            if envelope is None:
                continue
            target_envelope_id = envelope.id
            target_name = envelope.name
        else:
            goal_result = await db.execute(
                select(Goal).where(Goal.user_id == user.id, Goal.id == rule.target_id)
            )
            goal = goal_result.scalar_one_or_none()
            if goal is None:
                continue
            target_envelope_id = goal.envelope_id
            target_name = goal.name

        plan.append(
            DistributionPlanItem(
                rule_id=rule.id,
                target_type=rule.target_type,
                target_id=rule.target_id,
                target_name=target_name,
                to_envelope_id=target_envelope_id,
                amount=_quantize_amount(desired_amount),
            )
        )
        cash_remaining = _quantize_amount(cash_remaining - desired_amount)
        if cash_remaining <= 0:
            break

    if cash_remaining <= 0:
        return plan

    percent_rules = _percent_rules(rules)
    if apply_income_filter:
        percent_rules = [rule for rule in percent_rules if rule.auto_apply_on_income]

    valid_percent_rules = [
        rule
        for rule in percent_rules
        if Decimal(str(rule.percent or 0)) > Decimal("0.00")
    ]
    total_percent = sum(
        (Decimal(str(rule.percent or 0)) for rule in valid_percent_rules),
        Decimal("0.00"),
    )
    if total_percent <= 0:
        return plan

    if total_percent > Decimal("100"):
        expected_total = cash_remaining
        divisor = total_percent
    else:
        expected_total = _quantize_amount(cash_remaining * (total_percent / Decimal("100")))
        divisor = Decimal("100")

    running_total = Decimal("0.00")
    for index, rule in enumerate(valid_percent_rules):
        percent = Decimal(str(rule.percent or 0))

        if index == len(valid_percent_rules) - 1:
            desired_amount = _quantize_amount(expected_total - running_total)
            if desired_amount < 0:
                desired_amount = Decimal("0.00")
        else:
            desired_amount = _quantize_amount(expected_total * (percent / divisor))
            running_total = _quantize_amount(running_total + desired_amount)

        if desired_amount <= 0:
            continue

        if rule.target_type == "envelope":
            envelope_result = await db.execute(
                select(Envelope).where(
                    Envelope.user_id == user.id,
                    Envelope.id == rule.target_id,
                )
            )
            envelope = envelope_result.scalar_one_or_none()
            if envelope is None:
                continue
            target_envelope_id = envelope.id
            target_name = envelope.name
        else:
            goal_result = await db.execute(
                select(Goal).where(Goal.user_id == user.id, Goal.id == rule.target_id)
            )
            goal = goal_result.scalar_one_or_none()
            if goal is None:
                continue
            target_envelope_id = goal.envelope_id
            target_name = goal.name

        plan.append(
            DistributionPlanItem(
                rule_id=rule.id,
                target_type=rule.target_type,
                target_id=rule.target_id,
                target_name=target_name,
                to_envelope_id=target_envelope_id,
                amount=desired_amount,
            )
        )

    return plan


async def apply_distribution_plan(
    db: AsyncSession,
    user: User,
    ctx: DistributionContext,
    plan: list[DistributionPlanItem],
    trigger: str,
    transaction_id: Optional[UUID] = None,
    income_amount: Optional[Decimal] = None,
    config_id: Optional[UUID] = None,
    config_version: Optional[int] = None,
) -> DistributionLog:
    if trigger not in {"income_auto", "manual_apply"}:
        raise HTTPException(status_code=400, detail="Invalid trigger")

    cash_period_id = await _cash_period_id(db, user, ctx.occurred_on)
    cash_before = await cash_available_for_period(db, user, ctx.occurred_on)

    if trigger == "income_auto" and transaction_id is not None:
        existing_result = await db.execute(
            select(DistributionLog).where(
                DistributionLog.user_id == user.id,
                DistributionLog.trigger == "income_auto",
                DistributionLog.transaction_id == transaction_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing

    total_distributed = sum((item.amount for item in plan), Decimal("0.00"))
    total_distributed = _quantize_amount(total_distributed)
    if total_distributed <= 0:
        log = DistributionLog(
            user_id=user.id,
            period_start=ctx.period_start,
            period_end=ctx.period_end,
            trigger=trigger,
            transaction_id=transaction_id,
            income_amount=income_amount,
            cash_before=cash_before,
            cash_after=cash_before,
            config_id=config_id,
            config_version=config_version,
            status="skipped",
        )
        db.add(log)
        await db.flush()
        return log

    anchor_date = await resolve_user_sweep_anchor_date(db, user)

    persisted_rule_ids_result = await db.execute(
        select(DistributionRule.id).where(DistributionRule.user_id == user.id)
    )
    persisted_rule_ids = {rule_id for rule_id in persisted_rule_ids_result.scalars().all()}

    async with db.begin_nested():
        log = DistributionLog(
            user_id=user.id,
            period_start=ctx.period_start,
            period_end=ctx.period_end,
            trigger=trigger,
            transaction_id=transaction_id,
            income_amount=income_amount,
            cash_before=cash_before,
            cash_after=_quantize_amount(cash_before - total_distributed),
            config_id=config_id,
            config_version=config_version,
            status="applied",
        )
        db.add(log)
        await db.flush()

        from app.services.transactions import get_or_create_envelope_period

        for item in plan:
            target_period = await get_or_create_envelope_period(
                db,
                user.id,
                item.to_envelope_id,
                ctx.occurred_on,
                user.sweep_interval_days,
                anchor_date,
            )

            allocation = EnvelopeAllocation(
                user_id=user.id,
                envelope_period_id=target_period.id,
                amount=item.amount,
            )
            db.add(allocation)
            await db.flush()
            db.add(
                EnvelopeMovement(
                    user_id=user.id,
                    transaction_id=None,
                    envelope_period_id=cash_period_id,
                    amount=-item.amount,
                )
            )
            db.add(
                DistributionLogItem(
                    log_id=log.id,
                    rule_id=item.rule_id if item.rule_id in persisted_rule_ids else None,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    target_name=item.target_name,
                    amount=item.amount,
                    from_envelope_period_id=cash_period_id,
                    to_envelope_period_id=target_period.id,
                    allocation_id=allocation.id,
                )
            )

    return log
