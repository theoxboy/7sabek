from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, exists, select
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    CategoryEnvelopeMap,
    Envelope,
    EnvelopeAllocation,
    EnvelopeMovement,
    EnvelopePeriod,
    EnvelopeTransferLog,
    DistributionLog,
    DistributionLogItem,
    DistributionSavedConfig,
    Sweep,
    Transaction,
    TransactionType,
    User,
)
from app.services.balances import compute_period_balance
from app.services.periods import period_bounds
from app.services.sweep_context import resolve_user_sweep_anchor_date
from app.services.distribution_engine import (
    DistributionContext,
    apply_distribution_plan,
    build_distribution_plan,
    cash_available_for_period,
)
from app.services.distribution_effective_rules import get_effective_distribution_rules
from app.services.gamification import apply_transaction_scoring


async def resolve_envelope_for_category(
    db: AsyncSession, user_id: UUID, category_id: UUID
) -> Optional[Envelope]:
    mapping_result = await db.execute(
        select(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == user_id,
            CategoryEnvelopeMap.category_id == category_id,
        )
    )
    mapping = mapping_result.scalar_one_or_none()
    if mapping is not None:
        envelope_result = await db.execute(
            select(Envelope).where(
                Envelope.user_id == user_id,
                Envelope.id == mapping.envelope_id,
            )
        )
        envelope = envelope_result.scalar_one_or_none()
        if envelope is not None:
            return envelope

    return None


async def resolve_cash_envelope(db: AsyncSession, user_id: UUID) -> Envelope:
    result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user_id,
            Envelope.is_cash.is_(True),
        )
    )
    envelope = result.scalar_one_or_none()
    if envelope is not None:
        return envelope

    try:
        async with db.begin_nested():
            envelope = Envelope(
                user_id=user_id,
                name="Cash",
                is_cash=True,
                is_default_savings=False,
                deletable=False,
                rollover_enabled=False,
            )
            db.add(envelope)
            await db.flush()
            return envelope
    except IntegrityError:
        pass

    retry_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user_id,
            Envelope.is_cash.is_(True),
        )
    )
    envelope = retry_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=409, detail="CONCURRENT_MODIFICATION_RETRY")
    return envelope


async def resolve_default_savings_envelope(db: AsyncSession, user_id: UUID) -> Envelope:
    result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user_id,
            Envelope.is_default_savings.is_(True),
        )
    )
    envelope = result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=409, detail="DEFAULT_SAVINGS_ENVELOPE_MISSING")
    return envelope


async def get_or_create_envelope_period(
    db: AsyncSession,
    user_id: UUID,
    envelope_id: UUID,
    occurred_on: date,
    sweep_interval_days: int,
    anchor_date: date,
) -> EnvelopePeriod:
    existing_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_start <= occurred_on,
            EnvelopePeriod.period_end > occurred_on,
        ).order_by(EnvelopePeriod.period_start.desc())
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        return existing

    period_start, period_end = period_bounds(
        anchor_date, occurred_on, sweep_interval_days
    )
    result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period = result.scalars().first()
    if period is not None:
        return period

    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user_id,
            Envelope.id == envelope_id,
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    prev_result = await db.execute(
        select(EnvelopePeriod)
        .where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_end <= period_start,
        )
        .order_by(EnvelopePeriod.period_end.desc())
        .limit(1)
    )
    prev_period = prev_result.scalars().first()
    opening_balance = Decimal("0.00")
    rollover_from_period_id: Optional[UUID] = None

    pending_sweep: Optional[Sweep] = None
    pending_transfer_log: Optional[EnvelopeTransferLog] = None
    pending_swept_at: Optional[datetime] = None
    if prev_period is not None:
        prev_balance = await compute_period_balance(db, prev_period.id)
        closing_balance = prev_balance["closing_balance"]

        if envelope.is_cash or envelope.is_default_savings or envelope.rollover_enabled:
            opening_balance = closing_balance
            rollover_from_period_id = prev_period.id
        else:
            if closing_balance > 0:
                savings_envelope = await resolve_default_savings_envelope(db, user_id)
                savings_period = await get_or_create_envelope_period(
                    db=db,
                    user_id=user_id,
                    envelope_id=savings_envelope.id,
                    occurred_on=prev_period.period_start,
                    sweep_interval_days=sweep_interval_days,
                    anchor_date=anchor_date,
                )
                pending_sweep = Sweep(
                    user_id=user_id,
                    from_envelope_period_id=prev_period.id,
                    to_envelope_period_id=savings_period.id,
                    amount=closing_balance,
                    swept_on=prev_period.period_end,
                )
                pending_transfer_log = EnvelopeTransferLog(
                    user_id=user_id,
                    to_envelope_id=savings_envelope.id,
                    from_envelope_id=envelope.id,
                    from_envelope_name=envelope.name,
                    amount=closing_balance,
                    period_start=prev_period.period_start,
                    period_end=prev_period.period_end,
                )
                pending_swept_at = datetime.now(timezone.utc)

    try:
        async with db.begin_nested():
            period = EnvelopePeriod(
                user_id=user_id,
                envelope_id=envelope_id,
                period_start=period_start,
                period_end=period_end,
                opening_balance=opening_balance,
                rollover_from_period_id=rollover_from_period_id,
            )
            if pending_sweep is not None:
                db.add(pending_sweep)
            if pending_transfer_log is not None:
                db.add(pending_transfer_log)
            if pending_swept_at is not None and prev_period is not None:
                prev_period.swept_at = pending_swept_at
            db.add(period)
            await db.flush()
            return period
    except IntegrityError:
        pass

    retry_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period = retry_result.scalars().first()

    if (
        period is not None
        and pending_sweep is not None
        and prev_period is not None
    ):
        locked_prev_result = await db.execute(
            select(EnvelopePeriod)
            .where(EnvelopePeriod.id == prev_period.id)
            .with_for_update()
        )
        locked_prev = locked_prev_result.scalar_one_or_none()
        if locked_prev is None or locked_prev.swept_at is not None:
            return period

        existing_sweep_result = await db.execute(
            select(Sweep).where(
                Sweep.user_id == user_id,
                Sweep.from_envelope_period_id == pending_sweep.from_envelope_period_id,
                Sweep.to_envelope_period_id == pending_sweep.to_envelope_period_id,
                Sweep.amount == pending_sweep.amount,
                Sweep.swept_on == pending_sweep.swept_on,
            )
        )
        existing_sweep = existing_sweep_result.scalar_one_or_none()
        if existing_sweep is None:
            db.add(pending_sweep)
            if pending_transfer_log is not None:
                db.add(pending_transfer_log)
            locked_prev.swept_at = pending_swept_at or datetime.now(timezone.utc)

    if period is None:
        raise HTTPException(status_code=409, detail="CONCURRENT_MODIFICATION_RETRY")
    return period


async def create_transaction_with_effects(
    db: AsyncSession,
    user: User,
    category: Category,
    transaction_type: TransactionType,
    amount,
    occurred_on: date,
    description: Optional[str],
    source: str = "manual",
) -> Transaction:
    anchor_date = await resolve_user_sweep_anchor_date(db, user)
    transaction = Transaction(
        user_id=user.id,
        category_id=category.id,
        type=transaction_type,
        amount=amount,
        occurred_on=occurred_on,
        description=description,
        source=source,
        created_at=datetime.now(timezone.utc),
    )
    db.add(transaction)
    await db.flush()

    if transaction_type == TransactionType.EXPENSE:
        envelope = await resolve_envelope_for_category(db, user.id, category.id)
        if envelope is None:
            raise HTTPException(status_code=400, detail="CATEGORY_NOT_MAPPED")
        period = await get_or_create_envelope_period(
            db,
            user.id,
            envelope.id,
            occurred_on,
            user.sweep_interval_days,
            anchor_date,
        )
        movement = EnvelopeMovement(
            user_id=user.id,
            transaction_id=transaction.id,
            envelope_period_id=period.id,
            amount=-amount,
        )
        db.add(movement)
    elif transaction_type == TransactionType.INCOME:
        envelope = await resolve_cash_envelope(db, user.id)
        period = await get_or_create_envelope_period(
            db,
            user.id,
            envelope.id,
            occurred_on,
            user.sweep_interval_days,
            anchor_date,
        )
        movement = EnvelopeMovement(
            user_id=user.id,
            transaction_id=transaction.id,
            envelope_period_id=period.id,
            amount=amount,
        )
        db.add(movement)

        rules = await get_effective_distribution_rules(db, user)
        if rules:
            cash_available = await cash_available_for_period(db, user, occurred_on)
            ctx = DistributionContext(
                occurred_on=occurred_on,
                period_start=period.period_start,
                period_end=period.period_end,
            )
            plan = await build_distribution_plan(
                db=db,
                user=user,
                ctx=ctx,
                rules=rules,
                cash_available=cash_available,
                base_amount=amount,
                apply_income_filter=True,
            )
            active_config_result = await db.execute(
                select(DistributionSavedConfig)
                .where(
                    DistributionSavedConfig.user_id == user.id,
                    DistributionSavedConfig.is_active.is_(True),
                )
                .order_by(
                    DistributionSavedConfig.version.desc(),
                    DistributionSavedConfig.updated_at.desc(),
                )
                .limit(1)
            )
            active_config = active_config_result.scalar_one_or_none()
            await apply_distribution_plan(
                db=db,
                user=user,
                ctx=ctx,
                plan=plan,
                trigger="income_auto",
                transaction_id=transaction.id,
                income_amount=amount,
                config_id=active_config.id if active_config is not None else None,
                config_version=active_config.version if active_config is not None else None,
            )

    await apply_transaction_scoring(db, user, transaction)

    await db.commit()
    await db.refresh(transaction)
    return transaction


async def clear_income_distribution_effects(
    db: AsyncSession,
    *,
    user_id: UUID,
    transaction_id: UUID,
) -> None:
    log_result = await db.execute(
        select(DistributionLog).where(
            DistributionLog.user_id == user_id,
            DistributionLog.trigger == "income_auto",
            DistributionLog.transaction_id == transaction_id,
        )
    )
    logs = list(log_result.scalars().all())
    if not logs:
        return

    for log in logs:
        item_result = await db.execute(
            select(DistributionLogItem).where(DistributionLogItem.log_id == log.id)
        )
        items = list(item_result.scalars().all())
        for item in items:
            allocation = None
            if item.allocation_id is not None:
                allocation_result = await db.execute(
                    select(EnvelopeAllocation).where(
                        EnvelopeAllocation.id == item.allocation_id,
                        EnvelopeAllocation.user_id == user_id,
                    )
                )
                allocation = allocation_result.scalar_one_or_none()

            if allocation is None:
                allocation_result = await db.execute(
                    select(EnvelopeAllocation)
                    .where(
                        EnvelopeAllocation.user_id == user_id,
                        EnvelopeAllocation.envelope_period_id == item.to_envelope_period_id,
                        EnvelopeAllocation.amount == item.amount,
                        EnvelopeAllocation.created_at >= log.created_at,
                        EnvelopeAllocation.created_at <= item.created_at,
                        ~exists(
                            select(DistributionLogItem.id).where(
                                DistributionLogItem.allocation_id == EnvelopeAllocation.id
                            )
                        ),
                    )
                    .order_by(EnvelopeAllocation.created_at.desc())
                    .limit(1)
                )
                allocation = allocation_result.scalar_one_or_none()
            if allocation is not None:
                await db.delete(allocation)
            else:
                # Fallback if allocation row is already missing.
                db.add(
                    EnvelopeMovement(
                        user_id=user_id,
                        transaction_id=None,
                        envelope_period_id=item.to_envelope_period_id,
                        amount=-item.amount,
                    )
                )

            db.add(
                EnvelopeMovement(
                    user_id=user_id,
                    transaction_id=None,
                    envelope_period_id=item.from_envelope_period_id,
                    amount=item.amount,
                )
            )

        await db.execute(
            delete(DistributionLogItem).where(DistributionLogItem.log_id == log.id)
        )
        await db.delete(log)


async def apply_income_distribution_for_transaction(
    db: AsyncSession,
    *,
    user: User,
    transaction_id: UUID,
    amount: Decimal,
    occurred_on: date,
    period_start: date,
    period_end: date,
) -> None:
    if not user.auto_distribution_enabled:
        return
    rules = await get_effective_distribution_rules(db, user)
    if not rules:
        return
    cash_available = await cash_available_for_period(db, user, occurred_on)
    ctx = DistributionContext(
        occurred_on=occurred_on,
        period_start=period_start,
        period_end=period_end,
    )
    plan = await build_distribution_plan(
        db=db,
        user=user,
        ctx=ctx,
        rules=rules,
        cash_available=cash_available,
        base_amount=amount,
        apply_income_filter=True,
    )
    active_config_result = await db.execute(
        select(DistributionSavedConfig)
        .where(
            DistributionSavedConfig.user_id == user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
        .order_by(
            DistributionSavedConfig.version.desc(),
            DistributionSavedConfig.updated_at.desc(),
        )
        .limit(1)
    )
    active_config = active_config_result.scalar_one_or_none()
    await apply_distribution_plan(
        db=db,
        user=user,
        ctx=ctx,
        plan=plan,
        trigger="income_auto",
        transaction_id=transaction_id,
        income_amount=amount,
        config_id=active_config.id if active_config is not None else None,
        config_version=active_config.version if active_config is not None else None,
    )
