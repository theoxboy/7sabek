from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Envelope, EnvelopePeriod, EnvelopeMovement, Sweep, Transaction, TransactionType, User
from app.services.balances import compute_period_balance
from app.services.envelope_rules import is_sweep_eligible_envelope
from app.services.periods import period_bounds
from app.services.sweep_context import resolve_user_sweep_anchor_date
from app.services.category_catalog import INTERNAL_INCOME_CATEGORY_KEYS_SQL

logger = logging.getLogger(__name__)


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


async def _income_declared_in_window(
    db: AsyncSession, user_id: UUID, period_start: date, period_end: date
) -> bool:
    """Was a salary-type income declared in [period_start, period_end)?

    A cycle only closes once its income is on the books — otherwise a sweep
    would consolidate the previous cycle's leftovers before the user has told
    the app they were paid.
    """
    result = await db.execute(
        select(func.count(Transaction.id))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.INCOME,
            Category.name.in_(INTERNAL_INCOME_CATEGORY_KEYS_SQL),
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    return int(result.scalar_one()) > 0


async def _resolve_force_period_start(
    db: AsyncSession,
    user_id: UUID,
    envelope_id: UUID,
    as_of: date,
    sweep_interval_days: int,
) -> date:
    # Each envelope's currently-active period can have its own period_start
    # (periods are created lazily), so this must be resolved per envelope
    # rather than borrowed from an arbitrary row shared across envelopes.
    period_query = await db.execute(
        select(EnvelopePeriod.period_start).where(
            EnvelopePeriod.user_id == user_id,
            EnvelopePeriod.envelope_id == envelope_id,
            EnvelopePeriod.period_end == as_of,
        )
        .limit(1)
    )
    found_start = period_query.scalar_one_or_none()
    if found_start is not None:
        return found_start
    return as_of - timedelta(days=sweep_interval_days)


async def run_sweep(
    db: AsyncSession,
    user: User,
    as_of: date,
    force: bool = False,
    commit: bool = True,
) -> tuple[int, int]:
    """Sweep the balances of a closing period into savings.

    Pass commit=False when this runs as one step of a larger operation. A
    commit expires every ORM instance in the session, so committing mid-request
    leaves objects the caller still holds - the user row in particular - needing
    a lazy reload, which raises under async SQLAlchemy instead of reloading.
    """
    anchor = await resolve_user_sweep_anchor_date(db, user)
    if force:
        period_start = None  # resolved per envelope below
        period_end = as_of
    else:
        # period_end is exclusive; use the prior day to target the bucket ending at as_of.
        target_day = as_of - timedelta(days=1)
        period_start, period_end = period_bounds(
            anchor, target_day, user.sweep_interval_days
        )
        if period_end != as_of:
            raise ValueError("as_of must align with the exclusive period end")

        # A period does not close until its income has been declared. The
        # force path (early payday) is itself driven by an income declaration,
        # so it is exempt.
        if not await _income_declared_in_window(db, user.id, period_start, period_end):
            return 0, 0

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

    if force:
        savings_period_start = await _resolve_force_period_start(
            db, user.id, default_savings.id, as_of, user.sweep_interval_days
        )
    else:
        savings_period_start = period_start

    savings_period = await _get_or_create_period(
        db,
        user.id,
        default_savings.id,
        savings_period_start,
        period_end,
    )

    sweeps_created = 0
    periods_swept = 0

    for envelope in envelopes:
        if force:
            env_period_start = await _resolve_force_period_start(
                db, user.id, envelope.id, as_of, user.sweep_interval_days
            )
        else:
            env_period_start = period_start

        period = await _get_or_create_period(
            db,
            user.id,
            envelope.id,
            env_period_start,
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

        if force and anchor != period.period_end:
            next_start = period.period_end
            _, normal_end = period_bounds(anchor, env_period_start, user.sweep_interval_days)
            _, next_end = period_bounds(anchor, normal_end, user.sweep_interval_days)
        else:
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

    if commit:
        await db.commit()
    else:
        await db.flush()
    return periods_swept, sweeps_created


async def force_close_current_cycle(
    db: AsyncSession,
    user: User,
    early_date: date,
    permanent_shift: bool,
) -> None:
    if permanent_shift:
        from app.services.sweep_context import get_latest_onboarding_record
        record = await get_latest_onboarding_record(db, user.id)
        if record and isinstance(record.payload, dict):
            new_payload = dict(record.payload)
            new_payload["sweep_anchor_date"] = early_date.isoformat()
            record.payload = new_payload
            db.add(record)
            await db.flush()

    # Strictly greater: a period that began on early_date itself has not run for
    # a single day, and truncating it would set period_end == period_start,
    # which ck_env_period_date_range rejects and turns the whole declaration
    # into a 500. There is also nothing to close there - that period *is* the
    # cycle starting on this date - so it is left alone and the sweep below
    # handles it.
    active_periods_res = await db.execute(
        select(EnvelopePeriod)
        .where(
            EnvelopePeriod.user_id == user.id,
            EnvelopePeriod.period_start < early_date,
            EnvelopePeriod.period_end > early_date,
        )
    )
    active_periods = list(active_periods_res.scalars().all())
    for period in active_periods:
        period.period_end = early_date
        db.add(period)
    await db.flush()

    # No commit: this runs inside the transaction that is still recording the
    # income, and the request goes on to use ORM objects a commit would expire.
    await run_sweep(db, user, early_date, force=True, commit=False)


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

    # Nothing to preview until the period's income is declared.
    if not await _income_declared_in_window(db, user.id, period_start, period_end):
        return []

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
    # Take a per-user row lock to serialize concurrent auto-sweep entries. Note
    # the first run_sweep() below commits, which releases this lock, so a
    # multi-period backlog past the first period runs unlocked — the per-period
    # `already_swept` check and the IntegrityError catch are what prevent a
    # double sweep there, not this lock.
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
    for index, due_end in enumerate(due_period_ends):
        if index:
            # Every previous iteration ended in a commit or a rollback, and both
            # expire the ORM instances in the session. Reading an expired
            # attribute under async SQLAlchemy raises rather than reloading, so
            # the user is reloaded explicitly before its fields are read again.
            await db.refresh(locked_user)
        target_day = due_end - timedelta(days=1)
        period_start, _period_end = period_bounds(
            anchor, target_day, locked_user.sweep_interval_days
        )
        income_count_result = await db.execute(
            select(func.count(Transaction.id))
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.user_id == locked_user.id,
                Transaction.type == TransactionType.INCOME,
                Category.name.in_(INTERNAL_INCOME_CATEGORY_KEYS_SQL),
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


async def run_due_sweeps_tracked(
    db: AsyncSession, user: User, today: date
) -> tuple[int, int]:
    """`run_due_sweeps` wrapped so a failure is not silent.

    Auto-sweep runs opportunistically (login / transaction create) as a
    best-effort side effect. On failure this records a marker on the user so
    the dashboard can surface it; a later success clears it. Never raises.
    """
    user_id = user.id
    try:
        result = await run_due_sweeps(db, user, today)
    except Exception as exc:  # noqa: BLE001 - best-effort side effect
        try:
            await db.rollback()
        except Exception:
            pass
        try:
            await db.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    last_auto_sweep_error_at=func.now(),
                    last_auto_sweep_error=str(exc)[:500],
                )
            )
            await db.commit()
        except Exception:
            await db.rollback()
        logger.exception("auto_sweep_failed", extra={"user_id": str(user_id)})
        return 0, 0

    # Clear a stale marker only if one is set (no write, no commit otherwise).
    cleared = await db.execute(
        update(User)
        .where(User.id == user_id, User.last_auto_sweep_error_at.isnot(None))
        .values(last_auto_sweep_error_at=None, last_auto_sweep_error=None)
    )
    if cleared.rowcount:
        await db.commit()
    return result
