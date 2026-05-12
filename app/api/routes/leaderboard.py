from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User, UserGamification
from app.schemas.gamification import LeaderboardEntryOut, LeaderboardOut
from app.services.gamification import get_or_create_gamification

router = APIRouter(prefix="/leaderboard")


def display_name_for_user(user: User) -> str:
    if user.leaderboard_name and user.leaderboard_name.strip():
        return user.leaderboard_name.strip()
    if user.first_name and user.first_name.strip():
        return user.first_name.strip()
    suffix = str(user.id).replace("-", "")[-4:].upper()
    return f"User#{suffix}"


def eligible_query(points_field):
    return (
        select(User, points_field)
        .join(UserGamification, UserGamification.user_id == User.id)
        .where(
            User.role == "user",
            User.status == "active",
            User.deleted_at.is_(None),
            User.leaderboard_name.isnot(None),
            User.leaderboard_name != "",
        )
    )


async def build_leaderboard(
    period: str,
    points_field,
    db: AsyncSession,
    current_user: User,
) -> LeaderboardOut:
    top_result = await db.execute(
        eligible_query(points_field)
        .order_by(desc(points_field), User.created_at.asc())
        .limit(20)
    )
    entries: list[LeaderboardEntryOut] = []
    rank = 1
    for user, points in top_result.all():
        entries.append(
            LeaderboardEntryOut(
                rank=rank,
                display_name=display_name_for_user(user),
                points=int(points or 0),
            )
        )
        rank += 1

    gf = await get_or_create_gamification(db, current_user.id)
    opt_in = True
    user_rank = None
    user_points = None
    if current_user.leaderboard_name:
        points_value = int(getattr(gf, points_field.key))
        count_result = await db.execute(
            select(func.count())
            .select_from(User)
            .join(UserGamification, UserGamification.user_id == User.id)
            .where(
                User.role == "user",
                User.status == "active",
                User.deleted_at.is_(None),
                User.leaderboard_name.isnot(None),
                User.leaderboard_name != "",
                points_field > points_value,
            )
        )
        ahead = int(count_result.scalar_one())
        user_rank = ahead + 1
        user_points = points_value

    return LeaderboardOut(
        period=period,
        entries=entries,
        user_rank=user_rank,
        user_points=user_points,
        opt_in=opt_in,
    )


@router.get("/weekly", response_model=LeaderboardOut)
async def leaderboard_weekly(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeaderboardOut:
    return await build_leaderboard(
        "weekly", UserGamification.points_weekly, db, current_user
    )


@router.get("/monthly", response_model=LeaderboardOut)
async def leaderboard_monthly(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeaderboardOut:
    return await build_leaderboard(
        "monthly", UserGamification.points_monthly, db, current_user
    )


@router.get("/lifetime", response_model=LeaderboardOut)
async def leaderboard_lifetime(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeaderboardOut:
    return await build_leaderboard(
        "lifetime", UserGamification.points_total, db, current_user
    )
