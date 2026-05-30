from __future__ import annotations

from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import User
from app.schemas.email_center import (
    EmailCenterStatusOut,
    EmailDeliveryHistoryOut,
    EmailDeliveryOut,
    EmailDesignSettingsIn,
    EmailDesignSettingsOut,
    EmailDesignSettingsPatch,
    SendTestEmailIn,
)
from app.services.email_center import (
    get_delivery_history,
    get_or_create_design_settings,
    send_test_email,
)

router = APIRouter(prefix="/superadmin/email-center")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_enabled() -> None:
    if not get_settings().email_center_enabled:
        raise HTTPException(status_code=404, detail="Email center disabled")


@router.get("/status", response_model=EmailCenterStatusOut)
async def get_email_center_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailCenterStatusOut:
    _ = db
    _require_superadmin(current_user)
    _require_enabled()
    settings = get_settings()
    return EmailCenterStatusOut(
        enabled=settings.email_center_enabled,
        mode=settings.email_center_mode,
        kill_switch=settings.email_center_kill_switch,
        provider=settings.mail_provider,
        mail_from=settings.mail_from,
        test_recipient_email=settings.email_center_test_recipient_email,
        allow_bulk_send=settings.email_center_allow_bulk_send,
        allow_scheduling=settings.email_center_allow_scheduling,
        allow_salary_reminders=settings.email_center_allow_salary_reminders,
        allow_ai_suggestions=settings.email_center_ai_suggestions_enabled,
        allow_open_tracking=settings.email_center_allow_open_tracking,
        allow_click_tracking=settings.email_center_allow_click_tracking,
    )


@router.get("/design", response_model=EmailDesignSettingsOut)
async def get_email_design(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailDesignSettingsOut:
    _require_superadmin(current_user)
    _require_enabled()
    item = await get_or_create_design_settings(db)
    return EmailDesignSettingsOut.model_validate(item)


@router.post("/design", response_model=EmailDesignSettingsOut)
@router.patch("/design", response_model=EmailDesignSettingsOut)
async def upsert_email_design(
    payload: Union[EmailDesignSettingsIn, EmailDesignSettingsPatch],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailDesignSettingsOut:
    _require_superadmin(current_user)
    _require_enabled()
    item = await get_or_create_design_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return EmailDesignSettingsOut.model_validate(item)


@router.post("/send-test", response_model=EmailDeliveryOut)
async def send_email_test(
    payload: SendTestEmailIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailDeliveryOut:
    _require_superadmin(current_user)
    _require_enabled()
    if not payload.subject.strip() or not payload.body.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Subject and body are required",
        )

    try:
        delivery = await send_test_email(
            db,
            admin_user=current_user,
            to_email=str(payload.to),
            language=(payload.language or "fr").strip().lower(),
            subject=payload.subject,
            body=payload.body,
            cta_label=payload.cta_label,
            cta_url=payload.cta_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EmailDeliveryOut.model_validate(delivery)


@router.get("/history", response_model=EmailDeliveryHistoryOut)
async def get_email_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailDeliveryHistoryOut:
    _require_superadmin(current_user)
    _require_enabled()
    items, total = await get_delivery_history(db, page=page, page_size=page_size)
    return EmailDeliveryHistoryOut(
        items=[EmailDeliveryOut.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )
