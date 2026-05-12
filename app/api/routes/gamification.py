from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import PointsLog, User, UserGamification
from app.schemas.gamification import (
    GamificationLogOut,
    GamificationSettingsUpdate,
    GamificationSummaryOut,
)
from app.services.gamification import (
    compute_level,
    ensure_period_rollover,
    get_or_create_gamification,
    to_local_date,
    week_start,
    month_start,
)

router = APIRouter(prefix="/gamification")


def display_name_for_user(user: User) -> str:
    if user.leaderboard_name and user.leaderboard_name.strip():
        return user.leaderboard_name.strip()
    if user.first_name and user.first_name.strip():
        return user.first_name.strip()
    suffix = str(user.id).replace("-", "")[-4:].upper()
    return f"User#{suffix}"


def summary_from_gf(user: User, gf: UserGamification) -> GamificationSummaryOut:
    level, progress, next_points = compute_level(gf.points_total)
    label = f"L{level}"
    today = to_local_date(datetime.now(timezone.utc))
    week = gf.week_start or week_start(today)
    month = gf.month_start or month_start(today)
    return GamificationSummaryOut(
        points_total=gf.points_total,
        points_weekly=gf.points_weekly,
        points_monthly=gf.points_monthly,
        current_streak_days=gf.current_streak_days,
        longest_streak_days=gf.longest_streak_days,
        freeze_tokens=gf.freeze_tokens,
        freeze_pending=gf.freeze_pending_date is not None,
        freeze_pending_date=gf.freeze_pending_date,
        level=level,
        level_label=label,
        level_progress=progress,
        next_level_points=next_points,
        leaderboard_opt_in=True,
        display_name=display_name_for_user(user),
        week_start=week,
        month_start=month,
    )


@router.get("/summary", response_model=GamificationSummaryOut)
async def get_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GamificationSummaryOut:
    gf = await get_or_create_gamification(db, current_user.id)
    today = to_local_date(datetime.now(timezone.utc))
    await ensure_period_rollover(db, gf, today)
    await db.commit()
    await db.refresh(gf)
    return summary_from_gf(current_user, gf)


@router.patch("/settings", response_model=GamificationSummaryOut)
async def update_settings(
    payload: GamificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GamificationSummaryOut:
    gf = await get_or_create_gamification(db, current_user.id)
    gf.leaderboard_opt_in = True
    await db.commit()
    await db.refresh(gf)
    return summary_from_gf(current_user, gf)


@router.post("/freeze", response_model=GamificationSummaryOut)
async def use_freeze(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GamificationSummaryOut:
    gf = await get_or_create_gamification(db, current_user.id)
    today = to_local_date(datetime.now(timezone.utc))
    if gf.freeze_tokens <= 0 or gf.freeze_pending_date is None:
        raise HTTPException(status_code=400, detail="No freeze available")
    if gf.last_activity_date != today:
        raise HTTPException(status_code=400, detail="No activity today")
    if gf.freeze_pending_date != today - timedelta(days=1):
        raise HTTPException(status_code=400, detail="Freeze window expired")
    base_streak = gf.freeze_pending_streak or 0
    gf.current_streak_days = max(gf.current_streak_days, base_streak + 1)
    gf.longest_streak_days = max(gf.longest_streak_days, gf.current_streak_days)
    gf.freeze_tokens -= 1
    gf.freeze_pending_date = None
    gf.freeze_pending_streak = None
    await db.commit()
    await db.refresh(gf)
    return summary_from_gf(current_user, gf)


@router.get("/logs", response_model=list[GamificationLogOut])
async def list_logs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[GamificationLogOut]:
    result = await db.execute(
        select(PointsLog)
        .where(PointsLog.user_id == current_user.id)
        .order_by(desc(PointsLog.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/cron/weekly")
async def reset_weekly(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()
    token = getattr(settings, "gamification_cron_token", None)
    if token:
        header = request.headers.get("x-gamification-token")
        if header != token:
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="Forbidden")

    today = to_local_date(datetime.now(timezone.utc))
    current_week = week_start(today)
    await db.execute(
        UserGamification.__table__.update()
        .values(
            points_weekly=0,
            week_start=current_week,
            freeze_week_start=current_week,
            freeze_tokens=1,
        )
    )
    await db.commit()
    return {"status": "ok", "week_start": str(current_week)}


@router.post("/cron/monthly")
async def reset_monthly(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()
    token = getattr(settings, "gamification_cron_token", None)
    if token:
        header = request.headers.get("x-gamification-token")
        if header != token:
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="Forbidden")

    today = to_local_date(datetime.now(timezone.utc))
    current_month = month_start(today)
    await db.execute(
        UserGamification.__table__.update().values(
            points_monthly=0,
            month_start=current_month,
        )
    )
    await db.commit()
    return {"status": "ok", "month_start": str(current_month)}
