from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Envelope, EnvelopePeriod, Sweep, User
from app.schemas.sweep import SweepPreviewItem, SweepRun, SweepRunOut, SweepOut
from app.services.sweeps import preview_sweep, run_sweep
from app.services.gamification import award_fix_points_if_needed, to_local_date

router = APIRouter(prefix="/sweeps")

# `POST /sweeps` and `POST /sweeps/run` are aliases; keep one implementation.
_SWEEP_DATE_NOT_ALIGNED = (
    "SWEEP_DATE_NOT_ALIGNED: as_of must be a sweep period boundary."
)


async def _run_sweeps(
    payload: SweepRun,
    db: AsyncSession,
    current_user: User,
) -> SweepRunOut:
    # EnvelopePeriod.period_end is treated as exclusive end (start of next period).
    try:
        preview = await preview_sweep(db, current_user, payload.as_of)
        periods_swept, sweeps_created = await run_sweep(db, current_user, payload.as_of)
    except ValueError as exc:
        # e.g. as_of not aligned to the user's period grid. Surface a clean 400
        # instead of the generic 500 wrapper.
        raise HTTPException(status_code=400, detail=str(exc) or _SWEEP_DATE_NOT_ALIGNED) from exc
    if preview:
        await award_fix_points_if_needed(
            db,
            current_user,
            to_local_date(datetime.now(timezone.utc)),
            event_type="fix_sweep",
            points=30,
            meta={"sweeps_created": sweeps_created},
        )
        await db.commit()
    return SweepRunOut(periods_swept=periods_swept, sweeps_created=sweeps_created)


@router.post("", response_model=SweepRunOut)
async def run_sweeps_root(
    payload: SweepRun,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SweepRunOut:
    return await _run_sweeps(payload, db, current_user)


@router.post("/run", response_model=SweepRunOut)
async def run_sweeps(
    payload: SweepRun,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SweepRunOut:
    return await _run_sweeps(payload, db, current_user)


@router.get("", response_model=list[SweepOut])
async def list_sweeps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SweepOut]:
    from_period = aliased(EnvelopePeriod)
    to_period = aliased(EnvelopePeriod)
    from_envelope = aliased(Envelope)
    to_envelope = aliased(Envelope)

    result = await db.execute(
        select(Sweep, from_period, from_envelope, to_period, to_envelope)
        .join(from_period, from_period.id == Sweep.from_envelope_period_id)
        .join(from_envelope, from_envelope.id == from_period.envelope_id)
        .join(to_period, to_period.id == Sweep.to_envelope_period_id)
        .join(to_envelope, to_envelope.id == to_period.envelope_id)
        .where(Sweep.user_id == current_user.id)
        .order_by(Sweep.created_at.desc())
        .limit(50)
    )

    sweeps: list[SweepOut] = []
    for row in result.all():
        sweep = row[0]
        from_period = row[1]
        from_envelope = row[2]
        to_period = row[3]
        to_envelope = row[4]
        sweeps.append(
            SweepOut(
                id=sweep.id,
                amount=sweep.amount,
                swept_on=sweep.swept_on,
                created_at=sweep.created_at,
                from_envelope_id=from_period.envelope_id,
                from_envelope_name=from_envelope.name,
                to_envelope_id=to_period.envelope_id,
                to_envelope_name=to_envelope.name,
            )
        )
    return sweeps


@router.post("/preview", response_model=list[SweepPreviewItem])
async def preview_sweeps(
    payload: SweepRun,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SweepPreviewItem]:
    try:
        preview = await preview_sweep(db, current_user, payload.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or _SWEEP_DATE_NOT_ALIGNED) from exc
    return [SweepPreviewItem(**item) for item in preview]
