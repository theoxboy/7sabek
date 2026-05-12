from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    Category,
    Envelope,
    EnvelopeMovement,
    EnvelopePeriod,
    Transaction,
    TransactionType,
    User,
)
from app.schemas.reports import (
    ReportIncomeExpenseOut,
    ReportRange,
    ReportSpendingByCategoryOut,
    ReportSpendingByEnvelopeOut,
    ReportSummaryOut,
    ReportTopLabelOut,
)

router = APIRouter(prefix="/reports")


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(status_code=400, detail="end must be >= start")


@router.get("/summary", response_model=ReportSummaryOut)
async def get_report_summary(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportSummaryOut:
    _validate_range(start, end)

    income_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    income = Decimal(income_result.scalar_one())

    expense_result = await db.execute(
        select(func.coalesce(func.sum(-EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    expense = Decimal(expense_result.scalar_one())

    count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == current_user.id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    transactions_count = int(count_result.scalar_one())

    top_label_result = await db.execute(
        select(Transaction.description, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == current_user.id,
            Transaction.description.is_not(None),
            Transaction.description != "",
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Transaction.description)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(1)
    )
    top_label_row = top_label_result.first()
    top_label = top_label_row[0] if top_label_row else None

    return ReportSummaryOut(
        range=ReportRange(start=start, end=end),
        income=income,
        expense=expense,
        net=income - expense,
        transactions_count=transactions_count,
        top_label=top_label,
    )


@router.get("/income-expense", response_model=ReportIncomeExpenseOut)
async def get_income_vs_expense(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportIncomeExpenseOut:
    _validate_range(start, end)

    income_result = await db.execute(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    income = Decimal(income_result.scalar_one())

    expense_result = await db.execute(
        select(func.coalesce(func.sum(-EnvelopeMovement.amount), 0))
        .join(Transaction, Transaction.id == EnvelopeMovement.transaction_id)
        .where(
            EnvelopeMovement.user_id == current_user.id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
    )
    expense = Decimal(expense_result.scalar_one())

    return ReportIncomeExpenseOut(income=income, expense=expense, net=income - expense)


@router.get("/spending-by-envelope", response_model=list[ReportSpendingByEnvelopeOut])
async def spending_by_envelope(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReportSpendingByEnvelopeOut]:
    _validate_range(start, end)

    result = await db.execute(
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
            Transaction.type == TransactionType.EXPENSE,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Envelope.id, Envelope.name)
        .order_by(func.sum(-EnvelopeMovement.amount).desc())
    )

    return [
        ReportSpendingByEnvelopeOut(
            envelope_id=row[0],
            envelope_name=row[1],
            total=row[2],
        )
        for row in result.all()
    ]


@router.get("/spending-by-category", response_model=list[ReportSpendingByCategoryOut])
async def spending_by_category(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReportSpendingByCategoryOut]:
    _validate_range(start, end)

    result = await db.execute(
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
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(Transaction.amount).desc())
    )

    return [
        ReportSpendingByCategoryOut(
            category_id=row[0],
            category_name=row[1],
            total=row[2],
        )
        for row in result.all()
    ]


@router.get("/top-labels", response_model=list[ReportTopLabelOut])
async def top_labels(
    start: date = Query(...),
    end: date = Query(...),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReportTopLabelOut]:
    _validate_range(start, end)

    result = await db.execute(
        select(Transaction.description, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == current_user.id,
            Transaction.description.is_not(None),
            Transaction.description != "",
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .group_by(Transaction.description)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(limit)
    )

    return [
        ReportTopLabelOut(label=row[0], total=row[1]) for row in result.all()
    ]


@router.get("/export")
async def export_csv(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    _validate_range(start, end)

    result = await db.execute(
        select(
            Transaction.occurred_on,
            Transaction.type,
            Transaction.amount,
            Transaction.description,
        )
        .where(
            Transaction.user_id == current_user.id,
            Transaction.occurred_on >= start,
            Transaction.occurred_on <= end,
        )
        .order_by(Transaction.occurred_on.desc())
    )

    rows = ["date,type,amount,description"]
    for occurred_on, tx_type, amount, description in result.all():
        clean_description = (description or "").replace('"', '""')
        rows.append(f"{occurred_on},{tx_type},{amount},\"{clean_description}\"")

    csv_content = "\n".join(rows)
    headers = {"Content-Disposition": "attachment; filename=floussy-report.csv"}
    return Response(content=csv_content, media_type="text/csv", headers=headers)
