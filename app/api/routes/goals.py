from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import CategoryEnvelopeMap, Envelope, EnvelopePeriod, Goal, User
from app.schemas.goal import (
    GoalCreate,
    GoalDistributeRequest,
    GoalOut,
    GoalUpdate,
)
from app.services.goals import (
    compute_contribution_amount,
    distribute_income_to_goals,
    get_envelope_total_balance,
)
from app.services.envelope_rules import (
    is_reserved_envelope_name,
    name_key,
    normalize_name,
)

router = APIRouter(prefix="/goals")


def _normalize_goal_type(value: str | None) -> str:
    normalized = (value or "goal").strip().lower()
    return "sinking_fund" if normalized == "sinking_fund" else "goal"


async def _find_envelope_name_conflict(
    db: AsyncSession,
    user_id,
    candidate_name: str,
    exclude_envelope_id: UUID | None = None,
) -> Envelope | None:
    result = await db.execute(select(Envelope).where(Envelope.user_id == user_id))
    candidate_key = name_key(candidate_name)
    for envelope in result.scalars().all():
        if exclude_envelope_id is not None and envelope.id == exclude_envelope_id:
            continue
        if name_key(envelope.name) == candidate_key:
            return envelope
    return None


@router.get("", response_model=List[GoalOut])
async def list_goals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[GoalOut]:
    result = await db.execute(
        select(Goal).where(Goal.user_id == current_user.id).order_by(Goal.priority)
    )
    goals = list(result.scalars().all())
    payload: List[GoalOut] = []
    for goal in goals:
        current_balance = await get_envelope_total_balance(
            db, current_user.id, goal.envelope_id
        )
        payload.append(
            GoalOut.model_validate(goal).model_copy(
                update={"current_balance": current_balance}
            )
        )
    return payload


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GoalOut:
    normalized_name = normalize_name(payload.name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="GOAL_NAME_REQUIRED")
    if is_reserved_envelope_name(normalized_name):
        raise HTTPException(status_code=400, detail="GOAL_NAME_RESERVED")
    if await _find_envelope_name_conflict(db, current_user.id, normalized_name) is not None:
        raise HTTPException(status_code=400, detail="GOAL_NAME_EXISTS")

    contribution = (
        payload.contribution_amount
        if payload.contribution_amount is not None
        else compute_contribution_amount(
            payload.target_amount,
            payload.target_date,
            current_user.sweep_interval_days,
        )
    )

    envelope = Envelope(
        user_id=current_user.id,
        name=normalized_name,
        is_goal=True,
        is_default_savings=False,
        is_cash=False,
        deletable=True,
        rollover_enabled=True,
    )
    db.add(envelope)
    await db.flush()

    goal = Goal(
        user_id=current_user.id,
        envelope_id=envelope.id,
        name=normalized_name,
        goal_type=_normalize_goal_type(payload.goal_type),
        target_amount=payload.target_amount,
        target_date=payload.target_date,
        contribution_amount=contribution,
        auto_contribute=payload.auto_contribute,
        priority=payload.priority,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    current_balance = await get_envelope_total_balance(
        db, current_user.id, goal.envelope_id
    )
    return GoalOut.model_validate(goal).model_copy(
        update={"current_balance": current_balance}
    )


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: UUID,
    payload: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GoalOut:
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == current_user.id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == goal.envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="GOAL_ENVELOPE_NOT_FOUND")

    if payload.name is not None:
        normalized_name = normalize_name(payload.name)
        if not normalized_name:
            raise HTTPException(status_code=400, detail="GOAL_NAME_REQUIRED")
        if is_reserved_envelope_name(normalized_name):
            raise HTTPException(status_code=400, detail="GOAL_NAME_RESERVED")
        conflict = await _find_envelope_name_conflict(
            db,
            current_user.id,
            normalized_name,
            exclude_envelope_id=envelope.id,
        )
        if conflict is not None:
            raise HTTPException(status_code=400, detail="GOAL_NAME_EXISTS")
        goal.name = normalized_name
        envelope.name = normalized_name
    if payload.target_amount is not None:
        goal.target_amount = payload.target_amount
    if payload.goal_type is not None:
        goal.goal_type = _normalize_goal_type(payload.goal_type)
    if payload.target_date is not None:
        goal.target_date = payload.target_date
    if payload.auto_contribute is not None:
        goal.auto_contribute = payload.auto_contribute
    if payload.priority is not None:
        goal.priority = payload.priority

    if payload.contribution_amount is not None:
        goal.contribution_amount = payload.contribution_amount
    elif payload.target_amount is not None or payload.target_date is not None:
        goal.contribution_amount = compute_contribution_amount(
            goal.target_amount,
            goal.target_date,
            current_user.sweep_interval_days,
        )

    await db.commit()
    await db.refresh(goal)
    current_balance = await get_envelope_total_balance(
        db, current_user.id, goal.envelope_id
    )
    return GoalOut.model_validate(goal).model_copy(
        update={"current_balance": current_balance}
    )


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == current_user.id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == goal.envelope_id, Envelope.user_id == current_user.id
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is not None:
        mapping_count_result = await db.execute(
            select(func.count(CategoryEnvelopeMap.id)).where(
                CategoryEnvelopeMap.user_id == current_user.id,
                CategoryEnvelopeMap.envelope_id == envelope.id,
            )
        )
        mapping_count = int(mapping_count_result.scalar_one())
        period_count_result = await db.execute(
            select(func.count(EnvelopePeriod.id)).where(
                EnvelopePeriod.user_id == current_user.id,
                EnvelopePeriod.envelope_id == envelope.id,
            )
        )
        period_count = int(period_count_result.scalar_one())
        if mapping_count == 0 and period_count == 0:
            await db.delete(envelope)
        else:
            envelope.is_goal = False
    await db.delete(goal)
    await db.commit()


@router.post("/distribute", status_code=status.HTTP_204_NO_CONTENT)
async def distribute_goals(
    payload: GoalDistributeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(Goal).where(Goal.user_id == current_user.id, Goal.auto_contribute.is_(True))
    )
    goals = list(result.scalars().all())
    if not goals:
        return

    occurred_on = payload.occurred_on or date.today()
    if payload.amount is None:
        total = sum((goal.contribution_amount for goal in goals), Decimal("0.00"))
        amount = total
    else:
        amount = payload.amount

    if amount <= 0:
        return

    await distribute_income_to_goals(
        db=db,
        user=current_user,
        goals=goals,
        income_amount=amount,
        occurred_on=occurred_on,
    )
    await db.commit()
