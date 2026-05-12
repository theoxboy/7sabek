from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    Category,
    DistributionLog,
    DistributionRun,
    EnvelopeMovement,
    PointsLog,
    Transaction,
    TransactionType,
    User,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)
from app.services.transactions import (
    apply_income_distribution_for_transaction,
    clear_income_distribution_effects,
    create_transaction_with_effects,
    get_or_create_envelope_period,
    resolve_cash_envelope,
    resolve_envelope_for_category,
)
from app.services.sweep_context import resolve_user_sweep_anchor_date
from app.services.sweeps import run_due_sweeps
from app.services.gamification import to_local_date

router = APIRouter(prefix="/transactions")
logger = logging.getLogger("app.transactions")


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TransactionOut:
    category_result = await db.execute(
        select(Category).where(
            Category.id == payload.category_id,
            Category.user_id == current_user.id,
        )
    )
    category = category_result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    transaction_type = TransactionType(payload.type)
    transaction = await create_transaction_with_effects(
        db,
        current_user,
        category,
        transaction_type,
        payload.amount,
        payload.occurred_on,
        payload.description,
        payload.source,
    )

    result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.envelope_movement))
        .where(Transaction.id == transaction.id)
    )
    try:
        await run_due_sweeps(db, current_user, to_local_date(datetime.now(timezone.utc)))
    except Exception:
        logger.exception(
            "auto_sweep_failed_on_transaction_create",
            extra={"user_id": str(current_user.id), "transaction_id": str(transaction.id)},
        )
    return result.scalar_one()


@router.get("", response_model=list[TransactionOut])
async def list_transactions(
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TransactionOut]:
    query = (
        select(Transaction)
        .options(selectinload(Transaction.envelope_movement))
        .where(Transaction.user_id == current_user.id)
    )
    if start is not None:
        query = query.where(Transaction.occurred_on >= start)
    if end is not None:
        query = query.where(Transaction.occurred_on < end)
    query = query.order_by(desc(Transaction.occurred_on), desc(Transaction.created_at))
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.patch("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TransactionOut:
    result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.envelope_movement))
        .where(Transaction.id == transaction_id, Transaction.user_id == current_user.id)
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if payload.category_id is not None:
        category_result = await db.execute(
            select(Category).where(
                Category.id == payload.category_id,
                Category.user_id == current_user.id,
            )
        )
        if category_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Category not found")

    old_type = transaction.type

    new_type = (
        TransactionType(payload.type)
        if payload.type is not None
        else transaction.type
    )
    new_category_id = payload.category_id or transaction.category_id
    new_amount = payload.amount if payload.amount is not None else transaction.amount
    new_occurred_on = (
        payload.occurred_on if payload.occurred_on is not None else transaction.occurred_on
    )
    new_description = (
        payload.description
        if "description" in payload.model_fields_set
        else transaction.description
    )
    new_source = payload.source if payload.source is not None else transaction.source

    transaction.type = new_type
    transaction.category_id = new_category_id
    transaction.amount = new_amount
    transaction.occurred_on = new_occurred_on
    transaction.description = new_description
    transaction.source = new_source

    if old_type == TransactionType.INCOME:
        await clear_income_distribution_effects(
            db,
            user_id=current_user.id,
            transaction_id=transaction.id,
        )

    envelope = None
    period = None
    if new_type == TransactionType.EXPENSE:
        envelope = await resolve_envelope_for_category(
            db, current_user.id, new_category_id
        )
        if envelope is None:
            raise HTTPException(status_code=400, detail="CATEGORY_NOT_MAPPED")
    elif new_type == TransactionType.INCOME:
        envelope = await resolve_cash_envelope(db, current_user.id)

    await db.execute(
        delete(EnvelopeMovement).where(EnvelopeMovement.transaction_id == transaction.id)
    )

    if envelope is not None:
        anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
        period = await get_or_create_envelope_period(
            db,
            current_user.id,
            envelope.id,
            new_occurred_on,
            current_user.sweep_interval_days,
            anchor_date,
        )
        movement = EnvelopeMovement(
            user_id=current_user.id,
            transaction_id=transaction.id,
            envelope_period_id=period.id,
            amount=-new_amount if new_type == TransactionType.EXPENSE else new_amount,
        )
        db.add(movement)
        if new_type == TransactionType.INCOME and period is not None:
            await apply_income_distribution_for_transaction(
                db,
                user=current_user,
                transaction_id=transaction.id,
                amount=new_amount,
                occurred_on=new_occurred_on,
                period_start=period.period_start,
                period_end=period.period_end,
            )

    await db.commit()

    refreshed = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.envelope_movement))
        .where(Transaction.id == transaction.id)
    )
    return refreshed.scalar_one()


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    async with db.begin_nested():
        result = await db.execute(
            select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.user_id == current_user.id,
            )
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        if transaction.type == TransactionType.INCOME:
            await clear_income_distribution_effects(
                db,
                user_id=current_user.id,
                transaction_id=transaction.id,
            )

        await db.execute(
            delete(EnvelopeMovement).where(
                EnvelopeMovement.user_id == current_user.id,
                EnvelopeMovement.transaction_id == transaction_id,
            )
        )
        # Legacy schemas in some environments may not enforce ON DELETE SET NULL
        # on these foreign keys. Nullify them explicitly before deleting.
        await db.execute(
            update(DistributionLog)
            .where(
                DistributionLog.user_id == current_user.id,
                DistributionLog.transaction_id == transaction_id,
            )
            .values(transaction_id=None)
        )
        await db.execute(
            update(DistributionRun)
            .where(
                DistributionRun.user_id == current_user.id,
                DistributionRun.transaction_id == transaction_id,
            )
            .values(transaction_id=None)
        )
        await db.execute(
            update(PointsLog)
            .where(
                PointsLog.user_id == current_user.id,
                PointsLog.transaction_id == transaction_id,
            )
            .values(transaction_id=None)
        )
        await db.delete(transaction)
    await db.commit()
