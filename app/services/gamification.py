from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Envelope,
    EnvelopePeriod,
    Transaction,
    TransactionType,
    User,
    UserGamification,
    PointsLog,
)
from app.services.balances import compute_period_balance
from app.services.periods import period_bounds


USER_TZ = ZoneInfo("Africa/Casablanca")
DAILY_CAP = 20
MIN_AMOUNT = Decimal("0.50")
DUP_WINDOW_MINUTES = 5
DUP_LIMIT = 3

STREAK_BONUSES = {
    3: 15,
    7: 50,
    14: 120,
    30: 300,
}


def to_local_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(USER_TZ).date()


def day_bounds_utc(local_day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_day, time.min).replace(tzinfo=USER_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def week_start(local_day: date) -> date:
    return local_day - timedelta(days=local_day.weekday())


def month_start(local_day: date) -> date:
    return date(local_day.year, local_day.month, 1)


async def get_or_create_gamification(
    db: AsyncSession, user_id: UUID
) -> UserGamification:
    result = await db.execute(
        select(UserGamification).where(UserGamification.user_id == user_id)
    )
    gf = result.scalar_one_or_none()
    if gf is not None:
        if not gf.leaderboard_opt_in:
            gf.leaderboard_opt_in = True
            await db.flush()
        return gf
    gf = UserGamification(user_id=user_id)
    gf.leaderboard_opt_in = True
    db.add(gf)
    await db.flush()
    return gf


def compute_level(points_total: int) -> tuple[int, int, int]:
    if points_total < 200:
        return 1, points_total, 200
    if points_total < 500:
        return 2, points_total - 200, 300
    if points_total < 1000:
        return 3, points_total - 500, 500
    if points_total < 2000:
        return 4, points_total - 1000, 1000
    return 5, points_total - 2000, 0


async def ensure_period_rollover(
    db: AsyncSession, gf: UserGamification, today: date
) -> None:
    current_week = week_start(today)
    current_month = month_start(today)

    if gf.week_start != current_week:
        gf.week_start = current_week
        gf.points_weekly = 0
        gf.freeze_week_start = current_week
        gf.freeze_tokens = 1
        gf.freeze_pending_date = None
        gf.freeze_pending_streak = None

    if gf.month_start != current_month:
        gf.month_start = current_month
        gf.points_monthly = 0

    await db.flush()


async def sum_points_for_day(
    db: AsyncSession, user_id: UUID, local_day: date, scopes: Iterable[str]
) -> int:
    result = await db.execute(
        select(func.coalesce(func.sum(PointsLog.points), 0)).where(
            PointsLog.user_id == user_id,
            PointsLog.occurred_on == local_day,
            PointsLog.scope.in_(list(scopes)),
        )
    )
    return int(result.scalar_one())


async def has_log(
    db: AsyncSession, user_id: UUID, event_type: str, local_day: date
) -> bool:
    result = await db.execute(
        select(PointsLog.id).where(
            PointsLog.user_id == user_id,
            PointsLog.event_type == event_type,
            PointsLog.occurred_on == local_day,
        )
    )
    return result.scalar_one_or_none() is not None


async def award_points(
    db: AsyncSession,
    gf: UserGamification,
    user_id: UUID,
    points: int,
    event_type: str,
    local_day: date,
    scope: str,
    meta: Optional[dict] = None,
    transaction_id: Optional[UUID] = None,
) -> None:
    if points <= 0:
        return
    log = PointsLog(
        user_id=user_id,
        transaction_id=transaction_id,
        event_type=event_type,
        scope=scope,
        points=points,
        occurred_on=local_day,
        meta=meta,
    )
    db.add(log)
    gf.points_total += points
    gf.points_weekly += points
    gf.points_monthly += points
    await db.flush()


async def update_streak_on_activity(
    db: AsyncSession, gf: UserGamification, today: date
) -> None:
    last = gf.last_activity_date
    if last is None:
        gf.current_streak_days = 1
        gf.longest_streak_days = max(gf.longest_streak_days, 1)
        gf.last_activity_date = today
        gf.freeze_pending_date = None
        gf.freeze_pending_streak = None
        await db.flush()
        return

    if today == last:
        return

    diff = (today - last).days
    if diff == 1:
        gf.current_streak_days += 1
        gf.longest_streak_days = max(
            gf.longest_streak_days, gf.current_streak_days
        )
        gf.last_activity_date = today
        gf.freeze_pending_date = None
        gf.freeze_pending_streak = None
        await db.flush()
        return

    if diff == 2:
        gf.freeze_pending_date = last + timedelta(days=1)
        gf.freeze_pending_streak = gf.current_streak_days
        gf.current_streak_days = 1
        gf.last_activity_date = today
        await db.flush()
        return

    gf.current_streak_days = 1
    gf.last_activity_date = today
    gf.freeze_pending_date = None
    gf.freeze_pending_streak = None
    await db.flush()


async def apply_streak_bonus(
    db: AsyncSession, gf: UserGamification, user_id: UUID, today: date
) -> None:
    bonus = STREAK_BONUSES.get(gf.current_streak_days)
    if not bonus:
        return
    event_type = f"streak_{gf.current_streak_days}"
    if await has_log(db, user_id, event_type, today):
        return
    await award_points(
        db,
        gf,
        user_id,
        bonus,
        event_type,
        today,
        scope="streak",
        meta={"streak": gf.current_streak_days},
    )


async def should_skip_duplicate(
    db: AsyncSession,
    user_id: UUID,
    transaction: Transaction,
    now_utc: datetime,
) -> bool:
    window_start = now_utc - timedelta(minutes=DUP_WINDOW_MINUTES)
    result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == transaction.category_id,
            Transaction.type == transaction.type,
            Transaction.amount == transaction.amount,
            Transaction.created_at >= window_start,
        )
    )
    count = int(result.scalar_one())
    return count > DUP_LIMIT


async def apply_transaction_scoring(
    db: AsyncSession,
    user: User,
    transaction: Transaction,
) -> None:
    gf = await get_or_create_gamification(db, user.id)
    local_day = to_local_date(transaction.created_at or datetime.now(timezone.utc))

    await ensure_period_rollover(db, gf, local_day)
    await update_streak_on_activity(db, gf, local_day)
    await apply_streak_bonus(db, gf, user.id, local_day)

    if Decimal(str(transaction.amount)) < MIN_AMOUNT:
        return

    now_utc = transaction.created_at or datetime.now(timezone.utc)
    if await should_skip_duplicate(db, user.id, transaction, now_utc):
        return

    day_start, day_end = day_bounds_utc(local_day)
    tx_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.created_at >= day_start,
            Transaction.created_at < day_end,
        )
    )
    tx_count = int(tx_result.scalar_one())

    income_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.created_at >= day_start,
            Transaction.created_at < day_end,
            Transaction.type == TransactionType.INCOME,
        )
    )
    expense_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.created_at >= day_start,
            Transaction.created_at < day_end,
            Transaction.type == TransactionType.EXPENSE,
        )
    )
    income_count = int(income_result.scalar_one())
    expense_count = int(expense_result.scalar_one())

    events: list[tuple[str, int, str, dict | None]] = []
    if tx_count == 1 and not await has_log(db, user.id, "daily_base", local_day):
        events.append(("daily_base", 10, "daily", {"tx_count": tx_count}))
    if tx_count == 2 and not await has_log(db, user.id, "daily_multi", local_day):
        events.append(("daily_multi", 5, "daily", {"tx_count": tx_count}))
    if (
        income_count >= 1
        and expense_count >= 1
        and not await has_log(db, user.id, "daily_mixed", local_day)
    ):
        events.append(
            ("daily_mixed", 5, "daily", {"income": income_count, "expense": expense_count})
        )

    if transaction.description and transaction.description.strip():
        events.append(("tx_quality", 3, "tx", None))
    if transaction.source == "assistant":
        events.append(("tx_assistant", 5, "tx", None))

    if not events:
        return

    already = await sum_points_for_day(db, user.id, local_day, ("daily", "tx"))
    remaining = max(0, DAILY_CAP - already)
    for event_type, points, scope, meta in events:
        if points <= remaining:
            await award_points(
                db,
                gf,
                user.id,
                points,
                event_type,
                local_day,
                scope,
                meta=meta,
                transaction_id=transaction.id,
            )
            remaining -= points
        else:
            break


async def award_fix_points_if_needed(
    db: AsyncSession,
    user: User,
    local_day: date,
    event_type: str,
    points: int,
    meta: dict,
) -> None:
    gf = await get_or_create_gamification(db, user.id)
    await ensure_period_rollover(db, gf, local_day)
    if await has_log(db, user.id, event_type, local_day):
        return
    await award_points(
        db,
        gf,
        user.id,
        points,
        event_type,
        local_day,
        scope="fix",
        meta=meta,
    )


async def overspent_count_for_date(
    db: AsyncSession, user: User, for_date: date
) -> int:
    period_start, period_end = period_bounds(
        user.created_at.date(), for_date, user.sweep_interval_days
    )
    periods_result = await db.execute(
        select(EnvelopePeriod, Envelope)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .where(
            EnvelopePeriod.user_id == user.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    overspent = 0
    for period, envelope in periods_result.all():
        balance = await compute_period_balance(db, period.id)
        if balance["closing_balance"] < 0:
            overspent += 1
    return overspent
