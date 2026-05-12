from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import DateTime, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    Category,
    Envelope,
    EnvelopeMovement,
    EnvelopePeriod,
    PageView,
    Transaction,
    TransactionType,
    User,
)
from app.schemas.analytics import (
    ChurnBucketOut,
    MonthlyFinancePoint,
    OnboardingActivationOut,
    PageViewIn,
    PlatformAnalyticsOut,
    RolloverUsageOut,
    TopItemOut,
    TrafficDailyOut,
    TrafficSummaryOut,
    UserGrowthPoint,
    WeeklyActivePoint,
    FinanceDailyOut,
)

router = APIRouter(prefix="/analytics")


@router.post("/pageviews", status_code=status.HTTP_201_CREATED)
async def create_page_view(
    payload: PageViewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    source = (payload.source or "direct").strip().lower()
    view = PageView(
        user_id=current_user.id,
        path=payload.path[:255],
        referrer=payload.referrer,
        source=source or "direct",
    )
    db.add(view)
    await db.commit()
    return {"ok": True}


@router.get("/traffic", response_model=TrafficSummaryOut)
async def traffic_summary(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TrafficSummaryOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    today = date.today()
    start_date = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(today, time.max)

    previous_start = start_date - timedelta(days=days)
    previous_end = start_date - timedelta(days=1)
    previous_start_dt = datetime.combine(previous_start, time.min)
    previous_end_dt = datetime.combine(previous_end, time.max)

    result = await db.execute(
        select(func.date(PageView.created_at), func.count())
        .where(PageView.created_at >= start_dt, PageView.created_at <= end_dt)
        .group_by(func.date(PageView.created_at))
    )
    rows = {row[0].isoformat(): row[1] for row in result.all()}

    daily = []
    for index in range(days):
        day = start_date + timedelta(days=index)
        count = int(rows.get(day.isoformat(), 0))
        daily.append(TrafficDailyOut(date=day.isoformat(), count=count))

    total = sum(item.count for item in daily)

    previous_total_result = await db.execute(
        select(func.count())
        .where(PageView.created_at >= previous_start_dt)
        .where(PageView.created_at <= previous_end_dt)
    )
    previous_total = int(previous_total_result.scalar_one() or 0)

    sources_result = await db.execute(
        select(PageView.source, func.count())
        .where(PageView.created_at >= start_dt, PageView.created_at <= end_dt)
        .group_by(PageView.source)
    )
    sources = {row[0] or "direct": int(row[1]) for row in sources_result.all()}

    return TrafficSummaryOut(
        total=total,
        previous_total=previous_total,
        daily=daily,
        sources=sources,
    )


@router.get("/finance", response_model=list[FinanceDailyOut])
async def finance_summary(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FinanceDailyOut]:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    today = date.today()
    start_date = today - timedelta(days=days - 1)

    result = await db.execute(
        select(
            Transaction.occurred_on,
            func.sum(
                case(
                    (Transaction.type == TransactionType.INCOME, Transaction.amount),
                    else_=0,
                )
            ).label("income"),
            func.sum(
                case(
                    (Transaction.type == TransactionType.EXPENSE, Transaction.amount),
                    else_=0,
                )
            ).label("expense"),
        )
        .where(Transaction.occurred_on >= start_date)
        .group_by(Transaction.occurred_on)
        .order_by(Transaction.occurred_on)
    )

    return [
        FinanceDailyOut(
            date=row.occurred_on.isoformat(),
            income=float(row.income or 0),
            expense=float(row.expense or 0),
        )
        for row in result.all()
    ]


@router.get("/platform", response_model=PlatformAnalyticsOut)
async def platform_analytics(
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlatformAnalyticsOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    today = date.today()
    start_date = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(today, time.max)

    # User growth (daily new users).
    growth_result = await db.execute(
        select(func.date(User.created_at), func.count())
        .where(User.created_at >= start_dt, User.created_at <= end_dt)
        .group_by(func.date(User.created_at))
    )
    growth_rows = {row[0].isoformat(): int(row[1]) for row in growth_result.all()}
    user_growth = [
        UserGrowthPoint(date=(start_date + timedelta(days=i)).isoformat(),
                        count=growth_rows.get((start_date + timedelta(days=i)).isoformat(), 0))
        for i in range(days)
    ]

    # Weekly active users (last 8 weeks).
    week_start = today - timedelta(weeks=7)
    week_expr = func.date_trunc(
        "week", func.cast(Transaction.occurred_on, DateTime)
    )
    weekly_result = await db.execute(
        select(
            week_expr.label("week"),
            func.count(func.distinct(Transaction.user_id)),
        )
        .where(Transaction.occurred_on >= week_start)
        .group_by(week_expr)
        .order_by(week_expr)
    )
    weekly_rows = weekly_result.all()
    weekly_active = [
        WeeklyActivePoint(
            week=row.week.date().isoformat(),
            count=int(row[1]),
        )
        for row in weekly_rows
    ]

    # Monthly finance (last 12 months).
    month_start = today.replace(day=1) - timedelta(days=365)
    month_expr = func.date_trunc(
        "month", func.cast(Transaction.occurred_on, DateTime)
    )
    monthly_result = await db.execute(
        select(
            month_expr.label("month"),
            func.sum(
                case(
                    (Transaction.type == TransactionType.INCOME, Transaction.amount),
                    else_=0,
                )
            ).label("income"),
            func.sum(
                case(
                    (Transaction.type == TransactionType.EXPENSE, Transaction.amount),
                    else_=0,
                )
            ).label("expense"),
        )
        .where(Transaction.occurred_on >= month_start)
        .group_by(month_expr)
        .order_by(month_expr)
    )
    monthly_finance = [
        MonthlyFinancePoint(
            month=row.month.date().isoformat(),
            income=float(row.income or 0),
            expense=float(row.expense or 0),
        )
        for row in monthly_result.all()
    ]

    # Top categories (expenses).
    top_cat_result = await db.execute(
        select(Category.name, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(Transaction.type == TransactionType.EXPENSE)
        .group_by(Category.name)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10)
    )
    top_categories = [
        TopItemOut(name=row.name, total=float(row.total or 0))
        for row in top_cat_result.all()
    ]

    # Top envelopes (expenses via movements).
    top_env_result = await db.execute(
        select(Envelope.name, func.sum(func.abs(EnvelopeMovement.amount)).label("total"))
        .join(EnvelopePeriod, EnvelopePeriod.id == EnvelopeMovement.envelope_period_id)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .where(EnvelopeMovement.amount < 0)
        .group_by(Envelope.name)
        .order_by(func.sum(func.abs(EnvelopeMovement.amount)).desc())
        .limit(10)
    )
    top_envelopes = [
        TopItemOut(name=row.name, total=float(row.total or 0))
        for row in top_env_result.all()
    ]

    # Churn buckets based on last transaction date.
    last_tx_result = await db.execute(
        select(Transaction.user_id, func.max(Transaction.occurred_on))
        .group_by(Transaction.user_id)
    )
    last_tx_map = {row[0]: row[1] for row in last_tx_result.all()}
    user_result = await db.execute(select(User.id))
    buckets = {"0-7j": 0, "8-30j": 0, "31-60j": 0, "60j+": 0}
    for (user_id,) in user_result.all():
        last_date = last_tx_map.get(user_id)
        if last_date is None:
            buckets["60j+"] += 1
            continue
        days_since = (today - last_date).days
        if days_since <= 7:
            buckets["0-7j"] += 1
        elif days_since <= 30:
            buckets["8-30j"] += 1
        elif days_since <= 60:
            buckets["31-60j"] += 1
        else:
            buckets["60j+"] += 1
    churn = [ChurnBucketOut(label=label, count=count) for label, count in buckets.items()]

    # Onboarding activation counts.
    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    envelope_users = await db.scalar(
        select(func.count(func.distinct(Envelope.user_id)))
    ) or 0
    category_users = await db.scalar(
        select(func.count(func.distinct(Category.user_id)))
    ) or 0
    transaction_users = await db.scalar(
        select(func.count(func.distinct(Transaction.user_id)))
    ) or 0

    onboarding = OnboardingActivationOut(
        total_users=int(total_users),
        envelopes=int(envelope_users),
        categories=int(category_users),
        transactions=int(transaction_users),
    )

    rollover_on = await db.scalar(
        select(func.count()).select_from(Envelope).where(Envelope.rollover_enabled.is_(True))
    ) or 0
    rollover_off = await db.scalar(
        select(func.count()).select_from(Envelope).where(Envelope.rollover_enabled.is_(False))
    ) or 0
    rollover = RolloverUsageOut(on=int(rollover_on), off=int(rollover_off))

    # Average days to first transaction.
    first_tx_result = await db.execute(
        select(Transaction.user_id, func.min(Transaction.occurred_on))
        .group_by(Transaction.user_id)
    )
    first_tx_map = {row[0]: row[1] for row in first_tx_result.all()}
    total_days = 0
    count_days = 0
    user_dates = await db.execute(select(User.id, User.created_at))
    for user_id, created_at in user_dates.all():
        first_tx = first_tx_map.get(user_id)
        if first_tx is None:
            continue
        delta_days = (first_tx - created_at.date()).days
        if delta_days < 0:
            delta_days = 0
        total_days += delta_days
        count_days += 1
    avg_days = float(total_days / count_days) if count_days else 0.0

    return PlatformAnalyticsOut(
        user_growth=user_growth,
        weekly_active=weekly_active,
        monthly_finance=monthly_finance,
        top_categories=top_categories,
        top_envelopes=top_envelopes,
        churn=churn,
        onboarding=onboarding,
        rollover=rollover,
        avg_days_to_first_tx=avg_days,
    )
