from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    CategoryEnvelopeMap,
    DistributionItem,
    DistributionSavedConfig,
    Envelope,
    EnvelopeAdjustmentLog,
    EnvelopeAllocation,
    EnvelopeMovement,
    EnvelopePeriod,
    EnvelopeTransferLog,
    Sweep,
    User,
)
from app.schemas.allocation import (
    AllocationCreate,
    AllocationFromCashCreate,
    AllocationWithBalanceOut,
    PeriodBalanceOut,
)
from app.schemas.envelope import (
    EnvelopeAdjustmentCreate,
    EnvelopeCreate,
    EnvelopeOut,
    EnvelopeUpdate,
)
from app.schemas.envelope_adjustment_log import EnvelopeAdjustmentLogOut
from app.schemas.envelope_period import EnvelopePeriodOut
from app.schemas.envelope_transfer_log import EnvelopeTransferLogOut
from app.services.balances import compute_period_balance
from app.services.envelope_rules import (
    is_reserved_envelope_name,
    is_rollover_off_forbidden_envelope,
    name_key,
    normalize_name,
)
from app.services.envelope_virtual import is_virtual_parent_envelope_name
from app.services.transactions import get_or_create_envelope_period
from app.services.sweep_context import resolve_user_sweep_anchor_date
from app.services.gamification import (
    award_fix_points_if_needed,
    overspent_count_for_date,
    to_local_date,
)

router = APIRouter(prefix="/envelopes")


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


async def _has_active_fixed_distribution_for_envelope(
    db: AsyncSession,
    user_id: UUID,
    envelope_id: UUID,
) -> bool:
    active_saved_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == user_id,
            DistributionSavedConfig.is_active.is_(True),
        )
    )
    active_saved = active_saved_result.scalar_one_or_none()
    if active_saved is not None:
        for item in active_saved.rows if isinstance(active_saved.rows, list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("target_type")) != "envelope":
                continue
            if str(item.get("target_id")) != str(envelope_id):
                continue
            if not bool(item.get("enabled")):
                continue
            if str(item.get("mode")) != "fixed":
                continue
            amount_raw = item.get("fixed_amount")
            amount = Decimal(str(amount_raw or "0"))
            if amount > 0:
                return True
        return False

    item_result = await db.execute(
        select(DistributionItem).where(
            DistributionItem.user_id == user_id,
            DistributionItem.target_type == "envelope",
            DistributionItem.target_id == envelope_id,
            DistributionItem.enabled.is_(True),
            DistributionItem.mode == "fixed",
        )
    )
    for item in item_result.scalars().all():
        amount = Decimal(str(item.fixed_amount or "0"))
        if amount > 0:
            return True
    return False


@router.post("", response_model=EnvelopeOut, status_code=status.HTTP_201_CREATED)
async def create_envelope(
    payload: EnvelopeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EnvelopeOut:
    normalized_name = normalize_name(payload.name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="ENVELOPE_NAME_REQUIRED")
    if is_reserved_envelope_name(normalized_name):
        raise HTTPException(status_code=400, detail="ENVELOPE_NAME_RESERVED")
    if (
        await _find_envelope_name_conflict(db, current_user.id, normalized_name)
    ) is not None:
        raise HTTPException(status_code=400, detail="ENVELOPE_NAME_EXISTS")

    envelope = Envelope(
        user_id=current_user.id,
        name=normalized_name,
        rollover_enabled=payload.rollover_enabled,
    )
    db.add(envelope)
    await db.commit()
    await db.refresh(envelope)

    return envelope


@router.get("", response_model=list[EnvelopeOut])
async def list_envelopes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EnvelopeOut]:
    result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
        )
    )
    envelopes = list(result.scalars().all())
    return [env for env in envelopes if not is_virtual_parent_envelope_name(env.name)]


@router.patch("/{envelope_id}", response_model=EnvelopeOut)
async def update_envelope(
    envelope_id: UUID,
    payload: EnvelopeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EnvelopeOut:
    result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    if payload.name is not None:
        normalized_name = normalize_name(payload.name)
        if not normalized_name:
            raise HTTPException(status_code=400, detail="ENVELOPE_NAME_REQUIRED")
        if envelope.is_cash:
            raise HTTPException(status_code=400, detail="ENVELOPE_CASH_NAME_FIXED")
        if envelope.is_default_savings and name_key(normalized_name) != "epargnes":
            raise HTTPException(
                status_code=400, detail="ENVELOPE_DEFAULT_SAVINGS_NAME_FIXED"
            )
        if not envelope.is_default_savings and is_reserved_envelope_name(normalized_name):
            raise HTTPException(status_code=400, detail="ENVELOPE_NAME_RESERVED")
        if (
            await _find_envelope_name_conflict(
                db,
                current_user.id,
                normalized_name,
                exclude_envelope_id=envelope.id,
            )
        ) is not None:
            raise HTTPException(status_code=400, detail="ENVELOPE_NAME_EXISTS")
        envelope.name = normalized_name

    if payload.rollover_enabled is not None:
        if envelope.is_default_savings:
            raise HTTPException(
                status_code=400, detail="ENVELOPE_DEFAULT_SAVINGS_ROLLOVER_FIXED"
            )
        if envelope.is_cash:
            raise HTTPException(status_code=400, detail="ENVELOPE_CASH_ROLLOVER_FIXED")
        fixed_active = await _has_active_fixed_distribution_for_envelope(
            db, current_user.id, envelope.id
        )
        if payload.rollover_enabled is False and (
            is_rollover_off_forbidden_envelope(envelope) or fixed_active
        ):
            raise HTTPException(
                status_code=400,
                detail="ENVELOPE_ROLLOVER_OFF_FORBIDDEN_FOR_PROFILE",
            )
        envelope.rollover_enabled = payload.rollover_enabled

    await db.commit()
    await db.refresh(envelope)

    return envelope


@router.delete("/{envelope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_envelope(
    envelope_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    if envelope.is_default_savings or envelope.is_cash or not envelope.deletable:
        raise HTTPException(status_code=400, detail="ENVELOPE_CANNOT_DELETE")

    await db.execute(
        delete(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id,
            CategoryEnvelopeMap.envelope_id == envelope.id,
        )
    )

    cash_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(True),
        )
    )
    cash_envelope = cash_result.scalar_one_or_none()
    if cash_envelope is None:
        raise HTTPException(status_code=404, detail="Cash envelope not found")

    periods_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == envelope.id,
        )
    )
    periods = list(periods_result.scalars().all())
    if periods:
        for period in periods:
            balance = await compute_period_balance(db, period.id)
            if balance["closing_balance"] != 0:
                db.add(
                    EnvelopeTransferLog(
                        user_id=current_user.id,
                        to_envelope_id=cash_envelope.id,
                        from_envelope_id=envelope.id,
                        from_envelope_name=envelope.name,
                        amount=balance["closing_balance"],
                        period_start=period.period_start,
                        period_end=period.period_end,
                    )
                )

        cash_periods_result = await db.execute(
            select(EnvelopePeriod).where(
                EnvelopePeriod.user_id == current_user.id,
                EnvelopePeriod.envelope_id == cash_envelope.id,
            )
        )
        cash_periods = list(cash_periods_result.scalars().all())
        cash_period_map = {
            (period.period_start, period.period_end): period
            for period in cash_periods
        }

        period_id_map: dict[UUID, UUID] = {}
        for period in periods:
            key = (period.period_start, period.period_end)
            target = cash_period_map.get(key)
            if target is None:
                target = EnvelopePeriod(
                    user_id=current_user.id,
                    envelope_id=cash_envelope.id,
                    period_start=period.period_start,
                    period_end=period.period_end,
                    opening_balance=period.opening_balance,
                    swept_at=period.swept_at,
                )
                db.add(target)
                await db.flush()
                cash_period_map[key] = target
            else:
                await db.execute(
                    update(EnvelopePeriod)
                    .where(EnvelopePeriod.id == target.id)
                    .values(
                        opening_balance=EnvelopePeriod.opening_balance
                        + period.opening_balance
                    )
                )
            period_id_map[period.id] = target.id

        for source_id, target_id in period_id_map.items():
            await db.execute(
                update(EnvelopeAllocation)
                .where(EnvelopeAllocation.envelope_period_id == source_id)
                .values(envelope_period_id=target_id)
            )
            await db.execute(
                update(EnvelopeMovement)
                .where(EnvelopeMovement.envelope_period_id == source_id)
                .values(envelope_period_id=target_id)
            )

        source_ids = list(period_id_map.keys())
        if source_ids:
            await db.execute(
                delete(Sweep).where(
                    (Sweep.from_envelope_period_id.in_(source_ids))
                    | (Sweep.to_envelope_period_id.in_(source_ids))
                )
            )
            await db.execute(
                delete(EnvelopePeriod).where(EnvelopePeriod.id.in_(source_ids))
            )

    await db.delete(envelope)
    await db.commit()


@router.get(
    "/{envelope_id}/transfer-logs",
    response_model=list[EnvelopeTransferLogOut],
)
async def list_transfer_logs(
    envelope_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EnvelopeTransferLogOut]:
    result = await db.execute(
        select(EnvelopeTransferLog)
        .where(
            EnvelopeTransferLog.user_id == current_user.id,
            (EnvelopeTransferLog.to_envelope_id == envelope_id)
            | (EnvelopeTransferLog.from_envelope_id == envelope_id),
        )
        .order_by(EnvelopeTransferLog.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/{envelope_id}/allocate",
    response_model=AllocationWithBalanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def allocate_to_envelope(
    envelope_id: UUID,
    payload: AllocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AllocationWithBalanceOut:
    occurred_on = payload.occurred_on
    anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
    overspent_before = await overspent_count_for_date(db, current_user, occurred_on)
    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    period = await get_or_create_envelope_period(
        db,
        current_user.id,
        envelope.id,
        occurred_on,
        current_user.sweep_interval_days,
        anchor_date,
    )
    allocation = EnvelopeAllocation(
        user_id=current_user.id,
        envelope_period_id=period.id,
        amount=payload.amount,
    )
    db.add(allocation)
    await db.commit()
    await db.refresh(allocation)

    overspent_after = await overspent_count_for_date(db, current_user, occurred_on)
    if overspent_before > 0 and overspent_after == 0:
        await award_fix_points_if_needed(
            db,
            current_user,
            to_local_date(datetime.now(timezone.utc)),
            event_type="fix_overspent",
            points=20,
            meta={"from": overspent_before, "to": overspent_after},
        )
        await db.commit()

    balance = await compute_period_balance(db, period.id)
    return AllocationWithBalanceOut(allocation=allocation, balance=balance)


@router.post(
    "/{envelope_id}/adjust",
    response_model=PeriodBalanceOut,
)
async def adjust_envelope(
    envelope_id: UUID,
    payload: EnvelopeAdjustmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PeriodBalanceOut:
    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    occurred_on = payload.occurred_on or date.today()
    anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
    overspent_before = await overspent_count_for_date(db, current_user, occurred_on)
    period = await get_or_create_envelope_period(
        db,
        current_user.id,
        envelope.id,
        occurred_on,
        current_user.sweep_interval_days,
        anchor_date,
    )
    current_balance = await compute_period_balance(db, period.id)
    target = Decimal(str(payload.new_balance))
    delta = target - current_balance["closing_balance"]

    if delta == 0:
        return PeriodBalanceOut(**current_balance)

    if delta > 0:
        allocation = EnvelopeAllocation(
            user_id=current_user.id,
            envelope_period_id=period.id,
            amount=delta,
        )
        db.add(allocation)
    else:
        movement = EnvelopeMovement(
            user_id=current_user.id,
            transaction_id=None,
            envelope_period_id=period.id,
            amount=delta,
        )
        db.add(movement)

    db.add(
        EnvelopeAdjustmentLog(
            user_id=current_user.id,
            envelope_id=envelope.id,
            period_start=period.period_start,
            period_end=period.period_end,
            previous_balance=current_balance["closing_balance"],
            new_balance=target,
            delta=delta,
        )
    )

    await db.commit()

    overspent_after = await overspent_count_for_date(db, current_user, occurred_on)
    if overspent_before > 0 and overspent_after == 0:
        await award_fix_points_if_needed(
            db,
            current_user,
            to_local_date(datetime.now(timezone.utc)),
            event_type="fix_overspent",
            points=20,
            meta={"from": overspent_before, "to": overspent_after},
        )
        await db.commit()
    updated_balance = await compute_period_balance(db, period.id)
    return PeriodBalanceOut(**updated_balance)


@router.get(
    "/{envelope_id}/adjustment-logs",
    response_model=list[EnvelopeAdjustmentLogOut],
)
async def list_adjustment_logs(
    envelope_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EnvelopeAdjustmentLogOut]:
    result = await db.execute(
        select(EnvelopeAdjustmentLog)
        .where(
            EnvelopeAdjustmentLog.user_id == current_user.id,
            EnvelopeAdjustmentLog.envelope_id == envelope_id,
        )
        .order_by(EnvelopeAdjustmentLog.created_at.desc())
    )
    return list(result.scalars().all())


@router.get(
    "/{envelope_id}/periods/{period_id}/balance",
    response_model=PeriodBalanceOut,
)
async def get_period_balance(
    envelope_id: UUID,
    period_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PeriodBalanceOut:
    period_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.id == period_id,
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == envelope_id,
        )
    )
    period = period_result.scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")

    balance = await compute_period_balance(db, period.id)
    return PeriodBalanceOut(**balance)


@router.get(
    "/{envelope_id}/periods",
    response_model=list[EnvelopePeriodOut],
)
async def list_envelope_periods(
    envelope_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EnvelopePeriodOut]:
    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    if envelope_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Envelope not found")

    periods_result = await db.execute(
        select(EnvelopePeriod)
        .where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == envelope_id,
        )
        .order_by(EnvelopePeriod.period_start.desc())
    )
    periods = list(periods_result.scalars().all())

    results: list[EnvelopePeriodOut] = []
    for period in periods:
        balance = await compute_period_balance(db, period.id)
        results.append(
            EnvelopePeriodOut(
                id=period.id,
                period_start=period.period_start,
                period_end=period.period_end,
                opening_balance=balance["opening_balance"],
                total_allocations=balance["total_allocations"],
                total_spent=balance["total_spent"],
                closing_balance=balance["closing_balance"],
                swept_at=period.swept_at,
            )
        )

    return results


@router.post(
    "/{envelope_id}/allocate-from-cash",
    response_model=AllocationWithBalanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def allocate_from_cash(
    envelope_id: UUID,
    payload: AllocationFromCashCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AllocationWithBalanceOut:
    occurred_on = payload.occurred_on
    anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
    overspent_before = await overspent_count_for_date(db, current_user, occurred_on)
    target_result = await db.execute(
        select(Envelope).where(
            Envelope.id == envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    target_envelope = target_result.scalar_one_or_none()
    if target_envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")
    if target_envelope.is_cash:
        raise HTTPException(status_code=400, detail="Cash allocation is not allowed")

    cash_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(True),
        )
    )
    cash_envelope = cash_result.scalar_one_or_none()
    if cash_envelope is None:
        raise HTTPException(status_code=404, detail="Cash envelope not found")

    cash_period = await get_or_create_envelope_period(
        db,
        current_user.id,
        cash_envelope.id,
        occurred_on,
        current_user.sweep_interval_days,
        anchor_date,
    )
    target_period = await get_or_create_envelope_period(
        db,
        current_user.id,
        target_envelope.id,
        occurred_on,
        current_user.sweep_interval_days,
        anchor_date,
    )

    cash_balance = await compute_period_balance(db, cash_period.id)
    if cash_balance["closing_balance"] < payload.amount:
        raise HTTPException(status_code=400, detail="Insufficient cash balance")

    allocation = EnvelopeAllocation(
        user_id=current_user.id,
        envelope_period_id=target_period.id,
        amount=payload.amount,
    )
    db.add(allocation)

    cash_movement = EnvelopeMovement(
        user_id=current_user.id,
        transaction_id=None,
        envelope_period_id=cash_period.id,
        amount=-payload.amount,
    )
    db.add(cash_movement)

    await db.commit()
    await db.refresh(allocation)

    overspent_after = await overspent_count_for_date(db, current_user, occurred_on)
    if overspent_before > 0 and overspent_after == 0:
        await award_fix_points_if_needed(
            db,
            current_user,
            to_local_date(datetime.now(timezone.utc)),
            event_type="fix_overspent",
            points=20,
            meta={"from": overspent_before, "to": overspent_after},
        )
        await db.commit()

    balance = await compute_period_balance(db, target_period.id)
    return AllocationWithBalanceOut(allocation=allocation, balance=balance)
