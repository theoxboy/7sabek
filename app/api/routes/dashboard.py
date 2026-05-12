from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    Category,
    Envelope,
    EnvelopeAllocation,
    EnvelopeMovement,
    EnvelopePeriod,
    Sweep,
    Transaction,
    TransactionType,
    User,
)
from app.schemas.allocation import PeriodBalanceOut
from app.schemas.dashboard import (
    CurrentPeriodOut,
    DashboardOut,
    DashboardUserOut,
    DashboardAlertOut,
    DashboardDiagnosticsOut,
    DashboardSummaryOut,
    DashboardTrendPointOut,
    CashBalanceBreakdownOut,
    EnvelopeBalanceOut,
    SpendingByCategoryOut,
    SpendingByEnvelopeOut,
    SweepStatusOut,
)
from app.services.periods import period_bounds
from app.services.sweep_context import (
    build_sweep_bootstrap_status,
    resolve_user_sweep_anchor_date,
)
from app.services.envelope_virtual import is_virtual_parent_envelope_name
from app.services.category_mapping_integrity import ensure_system_category_mappings
from app.services.category_unmapped import count_manual_unmapped_categories

router = APIRouter(prefix="/dashboard")


async def _current_period_bounds(
    db: AsyncSession, current_user: User
) -> tuple[date, date]:
    today = date.today()
    anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
    return period_bounds(
        anchor_date,
        today,
        current_user.sweep_interval_days,
    )


async def _resolve_period_bounds(
    db: AsyncSession,
    current_user: User,
    start: Optional[date],
    end: Optional[date],
) -> tuple[date, date]:
    if start and end:
        if end < start:
            raise HTTPException(status_code=400, detail="end must be >= start")
        return start, end
    if start or end:
        raise HTTPException(status_code=400, detail="start and end are required")
    return await _current_period_bounds(db, current_user)


async def _compute_period_balances_batch(
    db: AsyncSession, periods: list[EnvelopePeriod]
) -> dict[UUID, dict[str, Decimal]]:
    if not periods:
        return {}

    period_ids = [period.id for period in periods]
    opening_balances = {
        period.id: Decimal(str(period.opening_balance)) for period in periods
    }

    allocations_result = await db.execute(
        select(
            EnvelopeAllocation.envelope_period_id,
            func.coalesce(func.sum(EnvelopeAllocation.amount), 0),
        )
        .where(EnvelopeAllocation.envelope_period_id.in_(period_ids))
        .group_by(EnvelopeAllocation.envelope_period_id)
    )
    allocations_by_period = {
        row[0]: Decimal(str(row[1])) for row in allocations_result.all()
    }

    movements_result = await db.execute(
        select(
            EnvelopeMovement.envelope_period_id,
            func.coalesce(func.sum(EnvelopeMovement.amount), 0),
        )
        .where(EnvelopeMovement.envelope_period_id.in_(period_ids))
        .group_by(EnvelopeMovement.envelope_period_id)
    )
    movements_by_period = {row[0]: Decimal(str(row[1])) for row in movements_result.all()}

    sweeps_out_result = await db.execute(
        select(
            Sweep.from_envelope_period_id,
            func.coalesce(func.sum(Sweep.amount), 0),
        )
        .where(Sweep.from_envelope_period_id.in_(period_ids))
        .group_by(Sweep.from_envelope_period_id)
    )
    sweeps_out_by_period = {
        row[0]: Decimal(str(row[1])) for row in sweeps_out_result.all()
    }

    sweeps_in_result = await db.execute(
        select(
            Sweep.to_envelope_period_id,
            func.coalesce(func.sum(Sweep.amount), 0),
        )
        .where(Sweep.to_envelope_period_id.in_(period_ids))
        .group_by(Sweep.to_envelope_period_id)
    )
    sweeps_in_by_period = {row[0]: Decimal(str(row[1])) for row in sweeps_in_result.all()}

    balances: dict[UUID, dict[str, Decimal]] = {}
    for period_id in period_ids:
        opening_balance = opening_balances.get(period_id, Decimal("0"))
        total_allocations = allocations_by_period.get(period_id, Decimal("0"))
        total_movements = movements_by_period.get(period_id, Decimal("0"))
        sweeps_out = sweeps_out_by_period.get(period_id, Decimal("0"))
        sweeps_in = sweeps_in_by_period.get(period_id, Decimal("0"))
        closing_balance = (
            opening_balance + total_allocations + total_movements - sweeps_out + sweeps_in
        )
        total_spent = -total_movements if total_movements < 0 else Decimal("0")
        balances[period_id] = {
            "opening_balance": opening_balance,
            "total_allocations": total_allocations,
            "total_spent": total_spent,
            "closing_balance": closing_balance,
        }
    return balances


async def _cash_balance_for_period(
    db: AsyncSession, current_user: User, period_start: date, period_end: date
) -> Decimal:
    cash_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(True),
        )
    )
    cash = cash_result.scalar_one_or_none()
    if cash is None:
        return Decimal("0")
    period_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == cash.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period = period_result.scalar_one_or_none()
    if period is None:
        return Decimal("0")
    balance_map = await _compute_period_balances_batch(db, [period])
    balance = balance_map.get(
        period.id,
        {
            "opening_balance": Decimal("0"),
            "total_allocations": Decimal("0"),
            "total_spent": Decimal("0"),
            "closing_balance": Decimal("0"),
        },
    )
    return balance["closing_balance"]


async def _cash_period_for_range(
    db: AsyncSession, current_user: User, period_start: date, period_end: date
) -> Optional[EnvelopePeriod]:
    cash_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(True),
        )
    )
    cash = cash_result.scalar_one_or_none()
    if cash is None:
        return None

    period_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == cash.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    return period_result.scalar_one_or_none()


async def _sweep_status_for_range(
    db: AsyncSession, current_user: User, period_start: date, period_end: date
) -> SweepStatusOut:
    income_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == current_user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    income_declared = int(income_result.scalar_one()) > 0

    swept_result = await db.execute(
        select(func.count(Sweep.id)).where(
            Sweep.user_id == current_user.id,
            Sweep.swept_on == period_end,
        )
    )
    already_swept = int(swept_result.scalar_one()) > 0

    due = date.today() >= period_end and income_declared and not already_swept
    return SweepStatusOut(
        due=due,
        period_start=period_start,
        period_end=period_end,
        income_declared=income_declared,
        already_swept=already_swept,
    )


async def _cash_breakdown(
    db: AsyncSession, period: Optional[EnvelopePeriod]
) -> CashBalanceBreakdownOut:
    if period is None:
        return CashBalanceBreakdownOut(
            period_id=None,
            opening_balance="0",
            total_allocations="0",
            total_movements="0",
            sweeps_out="0",
            sweeps_in="0",
            closing_balance="0",
        )

    allocations_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeAllocation.amount), 0)).where(
            EnvelopeAllocation.envelope_period_id == period.id
        )
    )
    total_allocations = Decimal(str(allocations_result.scalar_one()))

    movements_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), 0)).where(
            EnvelopeMovement.envelope_period_id == period.id
        )
    )
    total_movements = Decimal(str(movements_result.scalar_one()))

    sweeps_out_result = await db.execute(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.from_envelope_period_id == period.id
        )
    )
    sweeps_out = Decimal(str(sweeps_out_result.scalar_one()))

    sweeps_in_result = await db.execute(
        select(func.coalesce(func.sum(Sweep.amount), 0)).where(
            Sweep.to_envelope_period_id == period.id
        )
    )
    sweeps_in = Decimal(str(sweeps_in_result.scalar_one()))

    opening_balance = Decimal(str(period.opening_balance))
    closing_balance = (
        opening_balance + total_allocations + total_movements - sweeps_out + sweeps_in
    )

    return CashBalanceBreakdownOut(
        period_id=period.id,
        opening_balance=str(opening_balance),
        total_allocations=str(total_allocations),
        total_movements=str(total_movements),
        sweeps_out=str(sweeps_out),
        sweeps_in=str(sweeps_in),
        closing_balance=str(closing_balance),
    )


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardOut:
    period_start, period_end = await _resolve_period_bounds(
        db, current_user, start, end
    )
    sweep_status = await _sweep_status_for_range(
        db, current_user, period_start, period_end
    )
    sweep_bootstrap = await build_sweep_bootstrap_status(db, current_user)

    envelopes_result = await db.execute(
        select(Envelope)
        .where(Envelope.user_id == current_user.id)
        .order_by(Envelope.created_at.asc(), Envelope.id.asc())
    )
    envelopes = [
        envelope
        for envelope in envelopes_result.scalars().all()
        if not is_virtual_parent_envelope_name(envelope.name)
    ]

    periods_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    periods = {period.envelope_id: period for period in periods_result.scalars().all()}
    balances_by_period_id = await _compute_period_balances_batch(
        db, list(periods.values())
    )

    envelope_balances: list[EnvelopeBalanceOut] = []
    cash_balance_value = Decimal("0")
    net_worth_total = Decimal("0")
    for envelope in envelopes:
        period = periods.get(envelope.id)
        if period is None:
            balance_data = {
                "opening_balance": Decimal("0"),
                "total_allocations": Decimal("0"),
                "total_spent": Decimal("0"),
                "closing_balance": Decimal("0"),
            }
        else:
            balance_data = balances_by_period_id.get(
                period.id,
                {
                    "opening_balance": Decimal("0"),
                    "total_allocations": Decimal("0"),
                    "total_spent": Decimal("0"),
                    "closing_balance": Decimal("0"),
                },
            )

        net_worth_total += balance_data["closing_balance"]
        envelope_balances.append(
            EnvelopeBalanceOut(
                envelope=envelope,
                period_id=period.id if period else None,
                balance=PeriodBalanceOut(**balance_data),
            )
        )
        if envelope.is_cash:
            cash_balance_value = balance_data["closing_balance"]

    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == current_user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    period_income = Decimal(income_result.scalar_one())

    expense_result = await db.execute(
        select(func.coalesce(func.sum(-EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    period_expenses_mapped = Decimal(expense_result.scalar_one())
    period_net = period_income - period_expenses_mapped

    recent_result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.envelope_movement))
        .where(
            Transaction.user_id == current_user.id,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
        .order_by(Transaction.created_at.desc())
        .limit(10)
    )
    recent_transactions = list(recent_result.scalars().all())

    spending_by_category_result = await db.execute(
        select(
            Category.id,
            Category.name,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Category.user_id == current_user.id,
            Transaction.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
        .group_by(Category.id, Category.name)
    )
    spending_by_category = [
        SpendingByCategoryOut(
            category_id=row[0],
            category_name=row[1],
            total=str(row[2]),
        )
        for row in spending_by_category_result.all()
    ]

    spending_by_envelope_result = await db.execute(
        select(
            Envelope.id,
            Envelope.name,
            func.coalesce(func.sum(-EnvelopeMovement.amount), 0),
        )
        .join(EnvelopePeriod, EnvelopePeriod.id == EnvelopeMovement.envelope_period_id)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            EnvelopePeriod.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
        .group_by(Envelope.id, Envelope.name)
    )
    spending_by_envelope = [
        SpendingByEnvelopeOut(
            envelope_id=row[0],
            envelope_name=row[1],
            total=str(row[2]),
        )
        for row in spending_by_envelope_result.all()
    ]

    return DashboardOut(
        user=DashboardUserOut(
            id=current_user.id,
            email=current_user.email,
            currency=current_user.currency,
            sweep_interval_days=current_user.sweep_interval_days,
        ),
        current_period=CurrentPeriodOut(start=period_start, end=period_end),
        sweep_status=sweep_status,
        sweep_bootstrap=sweep_bootstrap,
        net_worth=str(net_worth_total),
        cash_balance=str(cash_balance_value),
        available_to_allocate=str(cash_balance_value),
        period_income=str(period_income),
        period_expenses_mapped=str(period_expenses_mapped),
        period_net=str(period_net),
        envelopes=envelope_balances,
        recent_transactions=recent_transactions,
        spending_by_category=spending_by_category,
        spending_by_envelope=spending_by_envelope,
    )


@router.get("/summary", response_model=DashboardSummaryOut)
async def get_dashboard_summary(
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardSummaryOut:
    period_start, period_end = await _resolve_period_bounds(
        db, current_user, start, end
    )

    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == current_user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    income = Decimal(income_result.scalar_one())

    expense_result = await db.execute(
        select(func.coalesce(func.sum(-EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
    )
    expenses_mapped = Decimal(expense_result.scalar_one())

    cash_balance = await _cash_balance_for_period(
        db, current_user, period_start, period_end
    )

    net = income - expenses_mapped
    return DashboardSummaryOut(
        period_start=period_start,
        period_end=period_end,
        income=str(income),
        expenses_mapped=str(expenses_mapped),
        net=str(net),
        cash_balance=str(cash_balance),
    )


@router.get("/alerts", response_model=DashboardAlertOut)
async def get_dashboard_alerts(
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardAlertOut:
    await ensure_system_category_mappings(db, current_user.id, repair=True)
    period_start, period_end = await _resolve_period_bounds(
        db, current_user, start, end
    )
    sweep_status = await _sweep_status_for_range(
        db, current_user, period_start, period_end
    )

    unmapped_categories = await count_manual_unmapped_categories(db, current_user.id)

    periods_result = await db.execute(
        select(EnvelopePeriod, Envelope)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period_rows = periods_result.all()
    balances_by_period_id = await _compute_period_balances_batch(
        db, [period for period, _ in period_rows]
    )
    overspent_envelopes: list[str] = []
    for period, envelope in period_rows:
        if is_virtual_parent_envelope_name(envelope.name):
            continue
        balance = balances_by_period_id.get(
            period.id,
            {
                "opening_balance": Decimal("0"),
                "total_allocations": Decimal("0"),
                "total_spent": Decimal("0"),
                "closing_balance": Decimal("0"),
            },
        )
        if balance["closing_balance"] < 0:
            overspent_envelopes.append(envelope.name)

    return DashboardAlertOut(
        unmapped_categories=unmapped_categories,
        overspent_envelopes=overspent_envelopes,
        sweep_due=sweep_status.due,
        current_period=CurrentPeriodOut(start=period_start, end=period_end),
        sweep_status=sweep_status,
    )


@router.get("/diagnostics", response_model=DashboardDiagnosticsOut)
async def get_dashboard_diagnostics(
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardDiagnosticsOut:
    period_start, period_end = await _resolve_period_bounds(
        db, current_user, start, end
    )
    cash_period = await _cash_period_for_range(db, current_user, period_start, period_end)
    breakdown = await _cash_breakdown(db, cash_period)
    cash_balance = Decimal(breakdown.closing_balance)
    return DashboardDiagnosticsOut(
        current_period=CurrentPeriodOut(start=period_start, end=period_end),
        cash_balance=str(cash_balance),
        cash_negative=cash_balance < 0,
        cash_breakdown=breakdown,
    )


@router.get("/trend", response_model=list[DashboardTrendPointOut])
async def get_dashboard_trend(
    limit: int = Query(default=6, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DashboardTrendPointOut]:
    period_result = await db.execute(
        select(EnvelopePeriod.period_start, EnvelopePeriod.period_end, Envelope.name)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .where(EnvelopePeriod.user_id == current_user.id)
        .order_by(EnvelopePeriod.period_start.desc())
    )
    distinct_periods: list[tuple[date, date]] = []
    seen_ranges: set[tuple[date, date]] = set()
    for period_start, period_end, envelope_name in period_result.all():
        if is_virtual_parent_envelope_name(envelope_name):
            continue
        key = (period_start, period_end)
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        distinct_periods.append(key)
        if len(distinct_periods) >= limit:
            break
    if not distinct_periods:
        return []

    range_pairs = [(period_start, period_end) for period_start, period_end in distinct_periods]
    periods_for_ranges_result = await db.execute(
        select(EnvelopePeriod, Envelope.name)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .where(
            EnvelopePeriod.user_id == current_user.id,
            tuple_(EnvelopePeriod.period_start, EnvelopePeriod.period_end).in_(
                range_pairs
            ),
        )
    )
    period_rows = [
        (period, envelope_name)
        for period, envelope_name in periods_for_ranges_result.all()
        if not is_virtual_parent_envelope_name(envelope_name)
    ]
    periods_for_ranges = [period for period, _ in period_rows]
    balances_by_period_id = await _compute_period_balances_batch(db, periods_for_ranges)
    net_worth_by_range: dict[tuple[date, date], Decimal] = {
        (period_start, period_end): Decimal("0")
        for period_start, period_end in distinct_periods
    }
    for period in periods_for_ranges:
        key = (period.period_start, period.period_end)
        balance = balances_by_period_id.get(
            period.id,
            {
                "opening_balance": Decimal("0"),
                "total_allocations": Decimal("0"),
                "total_spent": Decimal("0"),
                "closing_balance": Decimal("0"),
            },
        )
        net_worth_by_range[key] = net_worth_by_range.get(key, Decimal("0")) + balance[
            "closing_balance"
        ]

    results: list[DashboardTrendPointOut] = []
    for period_start, period_end in distinct_periods:
        net_worth = net_worth_by_range.get((period_start, period_end), Decimal("0"))
        results.append(
            DashboardTrendPointOut(
                period_start=period_start,
                period_end=period_end,
                net_worth=str(net_worth),
            )
        )

    results.reverse()
    return results


@router.get("/spending-by-envelope", response_model=list[SpendingByEnvelopeOut])
async def get_spending_by_envelope(
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SpendingByEnvelopeOut]:
    period_start, period_end = await _resolve_period_bounds(
        db, current_user, period_start, period_end
    )

    spending_by_envelope_result = await db.execute(
        select(
            Envelope.id,
            Envelope.name,
            func.coalesce(func.sum(-EnvelopeMovement.amount), 0),
        )
        .join(EnvelopePeriod, EnvelopePeriod.id == EnvelopeMovement.envelope_period_id)
        .join(Envelope, Envelope.id == EnvelopePeriod.envelope_id)
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            EnvelopePeriod.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= period_start,
            Transaction.occurred_on < period_end,
        )
        .group_by(Envelope.id, Envelope.name)
    )
    return [
        SpendingByEnvelopeOut(
            envelope_id=row[0],
            envelope_name=row[1],
            total=str(row[2]),
        )
        for row in spending_by_envelope_result.all()
        if not is_virtual_parent_envelope_name(row[1])
    ]
