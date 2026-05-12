from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Envelope, EnvelopePeriod, Sweep, Transaction, TransactionType, User
from app.services.balances import compute_period_balance
from app.services.envelope_rules import is_sweep_eligible_envelope
from app.services.periods import period_bounds
from app.services.sweep_context import resolve_user_sweep_anchor_date


async def _get_or_create_period(
    db: AsyncSession,
    user_id: UUID,
    envelope_id: UUID,
    period_start: date,
    period_end: date,
) -> EnvelopePeriod:
    result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period = result.scalar_one_or_none()
    if period is not None:
        return period

    period = EnvelopePeriod(
        user_id=user_id,
        envelope_id=envelope_id,
        period_start=period_start,
        period_end=period_end,
        opening_balance=0,
    )
    db.add(period)
    await db.flush()
    return period


async def run_sweep(db: AsyncSession, user: User, as_of: date) -> tuple[int, int]:
    anchor = await resolve_user_sweep_anchor_date(db, user)
    # period_end is exclusive; use the prior day to target the bucket ending at as_of.
    target_day = as_of - timedelta(days=1)
    period_start, period_end = period_bounds(
        anchor, target_day, user.sweep_interval_days
    )
    if period_end != as_of:
        raise ValueError("as_of must align with the exclusive period end")

    default_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user.id,
            Envelope.is_default_savings.is_(True),
        )
    )
    default_savings = default_result.scalar_one_or_none()
    if default_savings is None:
        raise ValueError("Default savings envelope not found")

    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user.id,
            Envelope.is_default_savings.is_(False),
        )
    )
    envelopes = [
        envelope
        for envelope in envelopes_result.scalars().all()
        if is_sweep_eligible_envelope(envelope)
    ]

    savings_period = await _get_or_create_period(
        db,
        user.id,
        default_savings.id,
        period_start,
        period_end,
    )

    sweeps_created = 0
    periods_swept = 0

    for envelope in envelopes:
        period = await _get_or_create_period(
            db,
            user.id,
            envelope.id,
            period_start,
            period_end,
        )

        balance = await compute_period_balance(db, period.id)
        if balance["closing_balance"] > 0:
            sweep = Sweep(
                user_id=user.id,
                from_envelope_period_id=period.id,
                to_envelope_period_id=savings_period.id,
                amount=balance["closing_balance"],
                swept_on=as_of,
            )
            db.add(sweep)
            sweeps_created += 1

        next_start, next_end = period_bounds(
            anchor, period.period_end, user.sweep_interval_days
        )
        await _get_or_create_period(
            db,
            user.id,
            envelope.id,
            next_start,
            next_end,
        )

        period.swept_at = datetime.now(timezone.utc)
        periods_swept += 1

    await db.commit()
    return periods_swept, sweeps_created


async def preview_sweep(
    db: AsyncSession, user: User, as_of: date
) -> list[dict[str, object]]:
    anchor = await resolve_user_sweep_anchor_date(db, user)
    target_day = as_of - timedelta(days=1)
    period_start, period_end = period_bounds(
        anchor, target_day, user.sweep_interval_days
    )
    if period_end != as_of:
        raise ValueError("as_of must align with the exclusive period end")

    default_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user.id,
            Envelope.is_default_savings.is_(True),
        )
    )
    default_savings = default_result.scalar_one_or_none()
    if default_savings is None:
        raise ValueError("Default savings envelope not found")

    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user.id,
            Envelope.is_default_savings.is_(False),
        )
    )
    envelopes = [
        envelope
        for envelope in envelopes_result.scalars().all()
        if is_sweep_eligible_envelope(envelope)
    ]

    preview: list[dict[str, object]] = []
    for envelope in envelopes:
        period_result = await db.execute(
            select(EnvelopePeriod).where(
                EnvelopePeriod.user_id == user.id,
                EnvelopePeriod.envelope_id == envelope.id,
                EnvelopePeriod.period_start == period_start,
                EnvelopePeriod.period_end == period_end,
            )
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            continue
        balance = await compute_period_balance(db, period.id)
        closing = balance["closing_balance"]
        if closing > 0:
            preview.append(
                {
                    "from_envelope_id": envelope.id,
                    "from_envelope_name": envelope.name,
                    "to_envelope_id": default_savings.id,
                    "to_envelope_name": default_savings.name,
                    "amount": closing,
                }
            )

    return preview


async def run_due_sweeps(
    db: AsyncSession,
    user: User,
    today: date,
) -> tuple[int, int]:
    # Serialize auto-sweep by user to reduce race conditions across concurrent requests.
    locked_user_result = await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )
    locked_user = locked_user_result.scalar_one_or_none()
    if locked_user is None or not locked_user.auto_sweep_enabled:
        return 0, 0

    anchor = await resolve_user_sweep_anchor_date(db, locked_user)

    # Build all period ends due up to `today`, then process oldest first.
    due_period_ends: list[date] = []
    _start, period_end = period_bounds(anchor, anchor, locked_user.sweep_interval_days)
    max_iters = 1024
    iters = 0
    while period_end <= today and iters < max_iters:
        due_period_ends.append(period_end)
        _start, period_end = period_bounds(
            anchor, period_end, locked_user.sweep_interval_days
        )
        iters += 1

    periods_swept_total = 0
    sweeps_created_total = 0
    for due_end in due_period_ends:
        target_day = due_end - timedelta(days=1)
        period_start, _period_end = period_bounds(
            anchor, target_day, locked_user.sweep_interval_days
        )
        income_count_result = await db.execute(
            select(func.count(Transaction.id)).where(
                Transaction.user_id == locked_user.id,
                Transaction.type == TransactionType.INCOME,
                Transaction.occurred_on >= period_start,
                Transaction.occurred_on < due_end,
            )
        )
        income_declared = int(income_count_result.scalar_one()) > 0
        if not income_declared:
            continue

        existing_sweep_result = await db.execute(
            select(func.count(Sweep.id)).where(
                Sweep.user_id == locked_user.id,
                Sweep.swept_on == due_end,
            )
        )
        already_swept = int(existing_sweep_result.scalar_one()) > 0
        if already_swept:
            continue

        try:
            periods_swept, sweeps_created = await run_sweep(db, locked_user, due_end)
        except IntegrityError:
            # Another request likely inserted the same sweep concurrently.
            await db.rollback()
            continue
        periods_swept_total += periods_swept
        sweeps_created_total += sweeps_created

    return periods_swept_total, sweeps_created_total
