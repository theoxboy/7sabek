from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.platform_settings import get_platform_settings, get_public_announcements
from app.db.session import get_db
from app.schemas.platform_settings import PlatformStatusOut

router = APIRouter(prefix="/public")


@router.get("/platform-status", response_model=PlatformStatusOut)
async def get_platform_status(
    db: AsyncSession = Depends(get_db),
) -> PlatformStatusOut:
    settings = await get_platform_settings(db, create_if_missing=False)
    announcements = get_public_announcements(settings)
    announcement_active = any(bool(item.get("active")) for item in announcements)
    primary_announcement = (
        next((item for item in announcements if item.get("active")), None)
        or (announcements[0] if announcements else None)
    )
    return PlatformStatusOut(
        platform_name=settings.platform_name,
        support_email=settings.support_email,
        guided_tours_enabled=settings.guided_tours_enabled,
        maintenance_mode=settings.maintenance_mode,
        advisor_tab_enabled=settings.advisor_tab_enabled,
        maintenance_message=settings.maintenance_message,
        announcement_enabled=settings.announcement_enabled,
        announcement_message=(
            primary_announcement["message"]
            if primary_announcement
            else settings.announcement_message
        ),
        announcement_type=(
            primary_announcement["type"]
            if primary_announcement
            else settings.announcement_type
        ),
        maintenance_placements=settings.maintenance_placements,
        announcement_placements=(
            primary_announcement["placements"]
            if primary_announcement
            else settings.announcement_placements
        ),
        announcement_active=announcement_active,
        announcement_start_at=(
            primary_announcement["start_at"]
            if primary_announcement
            else settings.announcement_start_at
        ),
        announcement_end_at=(
            primary_announcement["end_at"]
            if primary_announcement
            else settings.announcement_end_at
        ),
        announcement_timezone=(
            primary_announcement["timezone"]
            if primary_announcement
            else settings.announcement_timezone
        ),
        announcement_recurrence=(
            primary_announcement["recurrence"]
            if primary_announcement
            else settings.announcement_recurrence
        ),
        announcement_roles=(
            primary_announcement["roles"]
            if primary_announcement
            else settings.announcement_roles
        ),
        announcement_statuses=(
            primary_announcement["statuses"]
            if primary_announcement
            else settings.announcement_statuses
        ),
        announcement_countries=(
            primary_announcement["countries"]
            if primary_announcement
            else settings.announcement_countries
        ),
        announcements=announcements,
        account_deletion_grace_days=settings.account_deletion_grace_days,
        features={"passkeys": bool(get_settings().enable_passkeys)},
    )
