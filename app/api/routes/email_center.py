from __future__ import annotations

from typing import Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import EmailDelivery, EmailDesignSettings, User
from app.schemas.email_center import (
    EmailCenterStatusOut,
    EmailCenterSystemStatusCapabilitiesOut,
    EmailCenterSystemStatusDatabaseOut,
    EmailCenterSystemStatusFlagsOut,
    EmailCenterSystemStatusMailProviderOut,
    EmailCenterSystemStatusOut,
    EmailCenterSystemStatusSafetyOut,
    EmailCenterSystemStatusStatsOut,
    EmailCenterUserPreviewOut,
    EmailCenterUserSearchListOut,
    EmailCenterUserSearchOut,
    EmailDeliveryHistoryOut,
    EmailDeliveryOut,
    EmailDesignSettingsIn,
    EmailDesignSettingsOut,
    EmailDesignSettingsPatch,
    SendUserEmailIn,
    SendTestEmailIn,
)
from app.services.email_center import (
    build_user_display_name,
    detect_user_email_language,
    get_delivery_history,
    get_or_create_design_settings,
    get_user_by_id_for_email_center,
    render_email_html,
    search_users_for_email_center,
    send_user_email,
    send_test_email,
)

router = APIRouter(prefix="/superadmin/email-center")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_enabled() -> None:
    if not get_settings().email_center_enabled:
        raise HTTPException(status_code=404, detail="Email center disabled")


@router.get("/system-status", response_model=EmailCenterSystemStatusOut)
async def get_email_center_system_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailCenterSystemStatusOut:
    _require_superadmin(current_user)
    _require_enabled()
    settings = get_settings()

    design_table_ok = False
    deliveries_table_ok = False
    db_error = None
    try:
        await db.execute(select(EmailDesignSettings.id).limit(1))
        design_table_ok = True
    except Exception as exc:  # noqa: BLE001
        db_error = "email_design_settings_check_failed:{0}".format(type(exc).__name__)
    try:
        await db.execute(select(EmailDelivery.id).limit(1))
        deliveries_table_ok = True
    except Exception as exc:  # noqa: BLE001
        extra = "email_deliveries_check_failed:{0}".format(type(exc).__name__)
        db_error = "{0};{1}".format(db_error, extra) if db_error else extra

    total_deliveries = 0
    pending_count = 0
    sent_count = 0
    failed_count = 0
    skipped_count = 0
    latest_delivery_at = None
    if deliveries_table_ok:
        total_result = await db.execute(select(func.count(EmailDelivery.id)))
        total_deliveries = int(total_result.scalar_one() or 0)

        for status_key in ["pending", "sent", "failed", "skipped"]:
            count_result = await db.execute(
                select(func.count(EmailDelivery.id)).where(EmailDelivery.status == status_key)
            )
            count_value = int(count_result.scalar_one() or 0)
            if status_key == "pending":
                pending_count = count_value
            elif status_key == "sent":
                sent_count = count_value
            elif status_key == "failed":
                failed_count = count_value
            elif status_key == "skipped":
                skipped_count = count_value

        latest_result = await db.execute(select(func.max(EmailDelivery.created_at)))
        latest_delivery_at = latest_result.scalar_one_or_none()

    mode_value = (settings.email_center_mode or "").strip().lower()
    test_recipient_configured = bool((settings.email_center_test_recipient_email or "").strip())

    return EmailCenterSystemStatusOut(
        enabled=settings.email_center_enabled,
        mode=settings.email_center_mode,
        kill_switch=settings.email_center_kill_switch,
        flags=EmailCenterSystemStatusFlagsOut(
            ai_suggestions_enabled=settings.email_center_ai_suggestions_enabled,
            allow_user_send=settings.email_center_allow_user_send,
            allow_bulk_send=settings.email_center_allow_bulk_send,
            allow_scheduling=settings.email_center_allow_scheduling,
            allow_salary_reminders=settings.email_center_allow_salary_reminders,
            allow_open_tracking=settings.email_center_allow_open_tracking,
            allow_click_tracking=settings.email_center_allow_click_tracking,
        ),
        mail_provider=EmailCenterSystemStatusMailProviderOut(
            provider=settings.mail_provider,
            from_email=settings.mail_from,
            api_base_configured=bool((settings.mailtrap_api_base or "").strip()),
            token_configured=bool((settings.mailtrap_api_token or "").strip()),
        ),
        database=EmailCenterSystemStatusDatabaseOut(
            email_design_settings_table=design_table_ok,
            email_deliveries_table=deliveries_table_ok,
            error=db_error,
        ),
        capabilities=EmailCenterSystemStatusCapabilitiesOut(
            send_test=True,
            design_settings=True,
            history=True,
            user_search=True,
            user_preview=True,
            send_user=settings.email_center_allow_user_send and mode_value != "test_only",
            bulk_send=False,
            scheduling=False,
            salary_reminders=False,
            ai_suggestions=False,
        ),
        safety=EmailCenterSystemStatusSafetyOut(
            bulk_send_blocked=not settings.email_center_allow_bulk_send,
            scheduling_blocked=not settings.email_center_allow_scheduling,
            salary_reminders_blocked=not settings.email_center_allow_salary_reminders,
            test_recipient_configured=test_recipient_configured,
            production_send_enabled=(
                mode_value == "production" and settings.email_center_allow_user_send
            ),
        ),
        stats=EmailCenterSystemStatusStatsOut(
            total_deliveries=total_deliveries,
            pending=pending_count,
            sent=sent_count,
            failed=failed_count,
            skipped=skipped_count,
            latest_delivery_at=latest_delivery_at,
        ),
    )


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
        allow_user_send=settings.email_center_allow_user_send,
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


@router.get("/users/search", response_model=EmailCenterUserSearchListOut)
async def search_users(
    q: str = Query(default="", min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailCenterUserSearchListOut:
    _require_superadmin(current_user)
    _require_enabled()
    users = await search_users_for_email_center(db, query=q, limit=10)
    return EmailCenterUserSearchListOut(
        items=[
            EmailCenterUserSearchOut(
                id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                display_name=build_user_display_name(user),
                detected_language=detect_user_email_language(user),
            )
            for user in users
        ]
    )


@router.get("/users/{user_id}/preview", response_model=EmailCenterUserPreviewOut)
async def preview_user_email(
    user_id: UUID,
    subject: str = Query(..., min_length=1, max_length=300),
    body: str = Query(..., min_length=1, max_length=20000),
    cta_label: str = Query(default="", max_length=120),
    cta_url: str = Query(default="", max_length=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailCenterUserPreviewOut:
    _require_superadmin(current_user)
    _require_enabled()
    user = await get_user_by_id_for_email_center(db, user_id=user_id)
    if user is None or not (user.email or "").strip():
        raise HTTPException(status_code=404, detail="User not found")

    design = await get_or_create_design_settings(db)
    detected_language = detect_user_email_language(user)
    body_html, body_text = render_email_html(
        design=design,
        subject=subject,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )
    return EmailCenterUserPreviewOut(
        user_id=user.id,
        email=user.email,
        display_name=build_user_display_name(user),
        detected_language=detected_language,
        subject=subject.strip(),
        body_html=body_html,
        body_text=body_text,
    )


@router.post("/send-user", response_model=EmailDeliveryOut)
async def send_email_user(
    payload: SendUserEmailIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailDeliveryOut:
    _require_superadmin(current_user)
    _require_enabled()
    user = await get_user_by_id_for_email_center(db, user_id=payload.user_id)
    if user is None or not (user.email or "").strip():
        raise HTTPException(status_code=404, detail="User not found or missing email")
    if not payload.subject.strip() or not payload.body.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Subject and body are required",
        )
    try:
        delivery = await send_user_email(
            db,
            admin_user=current_user,
            user=user,
            subject=payload.subject,
            body=payload.body,
            cta_label=payload.cta_label,
            cta_url=payload.cta_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EmailDeliveryOut.model_validate(delivery)
