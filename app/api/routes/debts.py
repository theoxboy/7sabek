from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Debt, User
from app.schemas.debt import (
    DebtCreate,
    DebtOut,
    DebtRepaymentCreate,
    DebtUpdate,
)

router = APIRouter(prefix="/debts", tags=["Debts & Salaf"])


@router.get("", response_model=List[DebtOut])
async def list_debts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[DebtOut]:
    """
    Returns all debts and loans (Salaf) for the current user.
    """
    stmt = (
        select(Debt)
        .where(Debt.user_id == current_user.id)
        .order_by(desc(Debt.created_at))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=DebtOut, status_code=status.HTTP_201_CREATED)
async def create_debt(
    payload: DebtCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DebtOut:
    """
    Creates a new debt or loan record for the current user.
    """
    debt = Debt(
        user_id=current_user.id,
        contact_name=payload.contact_name.strip(),
        contact_phone=payload.contact_phone.strip() if payload.contact_phone else None,
        total_amount=payload.total_amount,
        paid_amount=payload.paid_amount,
        is_loan_given=payload.is_loan_given,
        due_date=payload.due_date,
        note=payload.note.strip() if payload.note else None,
    )
    db.add(debt)
    await db.commit()
    await db.refresh(debt)
    return debt


@router.patch("/{debt_id}", response_model=DebtOut)
async def update_debt(
    debt_id: UUID,
    payload: DebtUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DebtOut:
    """
    Updates an existing debt or loan record.
    """
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == current_user.id)
    )
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=404, detail="Debt not found")

    if payload.contact_name is not None:
        debt.contact_name = payload.contact_name.strip()
    if payload.contact_phone is not None:
        debt.contact_phone = payload.contact_phone.strip() if payload.contact_phone else None
    if payload.total_amount is not None:
        debt.total_amount = payload.total_amount
    if payload.paid_amount is not None:
        debt.paid_amount = payload.paid_amount
    if payload.is_loan_given is not None:
        debt.is_loan_given = payload.is_loan_given
    if payload.due_date is not None:
        debt.due_date = payload.due_date
    if payload.note is not None:
        debt.note = payload.note.strip() if payload.note else None

    await db.commit()
    await db.refresh(debt)
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_debt(
    debt_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Deletes a debt record.
    """
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == current_user.id)
    )
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=404, detail="Debt not found")

    await db.delete(debt)
    await db.commit()


@router.post("/{debt_id}/repay", response_model=DebtOut)
async def record_repayment(
    debt_id: UUID,
    payload: DebtRepaymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DebtOut:
    """
    Records a partial or full repayment for a debt.
    """
    result = await db.execute(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == current_user.id)
    )
    debt = result.scalar_one_or_none()
    if debt is None:
        raise HTTPException(status_code=404, detail="Debt not found")

    debt.paid_amount = min(debt.total_amount, debt.paid_amount + payload.amount)
    await db.commit()
    await db.refresh(debt)
    return debt
