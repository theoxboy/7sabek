from __future__ import annotations

from typing import Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.platform_settings import get_platform_settings
from app.db.session import get_db
from app.models import (
    EmailDelivery,
    EmailDesignSettings,
    Envelope,
    OnboardingV2Record,
    Transaction,
    User,
    UserPasskey,
)
try:
    from app.models.email_template import EmailTemplate
except Exception:  # pragma: no cover
    EmailTemplate = None  # type: ignore[assignment]
from app.schemas.email_center import (
    EmailCenterAISuggestIn,
    EmailCenterAISuggestOut,
    EmailCenterStatusOut,
    EmailCenterSystemStatusAIOut,
    EmailCenterSystemStatusCapabilitiesOut,
    EmailCenterSystemStatusDatabaseOut,
    EmailCenterSystemStatusFlagsOut,
    EmailCenterSystemStatusMailProviderOut,
    EmailCenterSystemStatusOut,
    EmailCenterSystemStatusSafetyOut,
    EmailCenterSystemStatusStatsOut,
    EmailCenterSystemStatusTemplatesOut,
    EmailCenterUserPreviewOut,
    EmailCenterUserSearchListOut,
    EmailCenterUserSearchOut,
    EmailDeliveryHistoryOut,
    EmailDeliveryOut,
    EmailDesignSettingsIn,
    EmailDesignSettingsOut,
    EmailDesignSettingsPatch,
    EmailTemplateCreate,
    EmailTemplateListOut,
    EmailTemplateOut,
    EmailTemplateUpdate,
    SendUserEmailIn,
    SendTestEmailIn,
)
from app.services.email_center import (
    build_user_display_name,
    detect_user_email_language,
    deactivate_email_template,
    create_email_template,
    get_email_template_by_id,
    get_delivery_history,
    get_or_create_design_settings,
    get_user_by_id_for_email_center,
    list_email_templates,
    render_email_html,
    search_users_for_email_center,
    seed_default_email_templates,
    send_user_email,
    send_test_email,
    update_email_template,
)
from app.services.ai_gateway_client import (
    AIGatewayConfigurationError,
    AIGatewayUnsupportedProviderError,
    AI_NOT_CONFIGURED_MESSAGE,
    get_ai_gateway_status,
    suggest_email_draft_via_gateway,
)

router = APIRouter(prefix="/superadmin/email-center")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_enabled() -> None:
    if not get_settings().email_center_enabled:
        raise HTTPException(status_code=404, detail="Email center disabled")


def _templates_enabled() -> bool:
    return bool(get_settings().email_center_templates_enabled) and EmailTemplate is not None


def _require_templates_enabled_for_write() -> None:
    if not _templates_enabled():
        raise HTTPException(status_code=403, detail="Email templates disabled")


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
    templates_count = 0
    active_templates_count = 0
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
    if EmailTemplate is not None:
        try:
            template_total_result = await db.execute(select(func.count(EmailTemplate.id)))
            templates_count = int(template_total_result.scalar_one() or 0)
            active_template_result = await db.execute(
                select(func.count(EmailTemplate.id)).where(EmailTemplate.is_active.is_(True))
            )
            active_templates_count = int(active_template_result.scalar_one() or 0)
        except Exception:
            templates_count = 0
            active_templates_count = 0

    mode_value = (settings.email_center_mode or "").strip().lower()
    test_recipient_configured = bool((settings.email_center_test_recipient_email or "").strip())
    platform_settings = await get_platform_settings(db, create_if_missing=False)
    ai_status = get_ai_gateway_status(platform_settings)
    ai_enabled = bool(settings.email_center_ai_suggestions_enabled)
    ai_configured = bool(ai_status["ai_gateway_configured"])
    ai_model_configured = bool(ai_status["ai_default_model_configured"])
    ai_capability = "ready" if (ai_enabled and ai_configured and ai_model_configured) else (
        "disabled" if not ai_enabled else "missing_config"
    )
    templates_enabled = bool(settings.email_center_templates_enabled)
    if EmailTemplate is None:
        templates_capability = "not_implemented"
    elif not templates_enabled:
        templates_capability = "disabled"
    elif active_templates_count > 0:
        templates_capability = "ready"
    else:
        templates_capability = "no_templates"

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
            templates_enabled=templates_enabled,
            allow_open_tracking=settings.email_center_allow_open_tracking,
            allow_click_tracking=settings.email_center_allow_click_tracking,
        ),
        mail_provider=EmailCenterSystemStatusMailProviderOut(
            provider=settings.mail_provider,
            from_email=settings.mail_from,
            api_base_configured=bool((settings.mailtrap_api_base or "").strip()),
            token_configured=bool((settings.mailtrap_api_token or "").strip()),
        ),
        ai=EmailCenterSystemStatusAIOut(
            ai_suggestions_enabled=ai_enabled,
            ai_gateway_configured=ai_configured,
            ai_default_model_configured=ai_model_configured,
            ai_capability=ai_capability,
        ),
        templates=EmailCenterSystemStatusTemplatesOut(
            templates_enabled=templates_enabled,
            templates_count=templates_count,
            active_templates_count=active_templates_count,
            templates_capability=templates_capability,
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
            ai_suggestions=ai_capability == "ready",
            templates=templates_capability == "ready",
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
        templates_enabled=settings.email_center_templates_enabled,
        allow_open_tracking=settings.email_center_allow_open_tracking,
        allow_click_tracking=settings.email_center_allow_click_tracking,
    )


@router.get("/templates", response_model=EmailTemplateListOut)
async def get_templates(
    language: str = Query(default=""),
    category: str = Query(default=""),
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateListOut:
    _require_superadmin(current_user)
    _require_enabled()
    if not _templates_enabled():
        return EmailTemplateListOut(enabled=False, items=[])
    items = await list_email_templates(
        db,
        language=(language or "").strip() or None,
        category=(category or "").strip() or None,
        active_only=bool(active_only),
    )
    return EmailTemplateListOut(enabled=True, items=[EmailTemplateOut.model_validate(item) for item in items])


@router.post("/templates", response_model=EmailTemplateOut)
async def create_template(
    payload: EmailTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateOut:
    _require_superadmin(current_user)
    _require_enabled()
    _require_templates_enabled_for_write()
    try:
        item = await create_email_template(
            db,
            admin_user=current_user,
            key=payload.key,
            name=payload.name,
            category=payload.category,
            language=payload.language,
            subject=payload.subject,
            preview_text=payload.preview_text,
            body=payload.body,
            cta_label=payload.cta_label,
            cta_url=payload.cta_url,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EmailTemplateOut.model_validate(item)


@router.patch("/templates/{template_id}", response_model=EmailTemplateOut)
async def patch_template(
    template_id: UUID,
    payload: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateOut:
    _require_superadmin(current_user)
    _require_enabled()
    _require_templates_enabled_for_write()
    item = await get_email_template_by_id(db, template_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        updated = await update_email_template(db, template=item, updates=payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EmailTemplateOut.model_validate(updated)


@router.delete("/templates/{template_id}", response_model=EmailTemplateOut)
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateOut:
    _require_superadmin(current_user)
    _require_enabled()
    _require_templates_enabled_for_write()
    item = await get_email_template_by_id(db, template_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Template not found")
    updated = await deactivate_email_template(db, item)
    return EmailTemplateOut.model_validate(updated)


@router.post("/templates/seed-defaults")
async def seed_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _require_superadmin(current_user)
    _require_enabled()
    _require_templates_enabled_for_write()
    inserted = await seed_default_email_templates(db, admin_user=current_user)
    return {"status": "ok", "inserted": inserted}


@router.post("/ai-suggest", response_model=EmailCenterAISuggestOut)
async def ai_suggest_email_draft(
    payload: EmailCenterAISuggestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailCenterAISuggestOut:
    _require_superadmin(current_user)
    _require_enabled()
    settings = get_settings()

    if not settings.email_center_ai_suggestions_enabled:
        raise HTTPException(status_code=403, detail="AI suggestions disabled")

    language = (payload.language or "fr").strip().lower()
    if language not in {"darija", "fr", "en"}:
        raise HTTPException(status_code=422, detail="Invalid language")
    tone = (payload.tone or "friendly").strip().lower()
    if tone not in {"friendly", "professional", "motivational", "short"}:
        raise HTTPException(status_code=422, detail="Invalid tone")
    audience_type = (payload.audience_type or "test").strip().lower()
    if audience_type not in {"test", "single_user"}:
        raise HTTPException(status_code=422, detail="Invalid audience_type")

    safe_user_context = None
    if audience_type == "single_user":
        if payload.user_id is None:
            raise HTTPException(status_code=422, detail="user_id is required for single_user")
        user = await get_user_by_id_for_email_center(db, user_id=payload.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        first_name = (getattr(user, "first_name", None) or "").strip()
        safe_user_context = {
            "audience_type": "single_user",
            "detected_language": detect_user_email_language(user),
            "onboarding_completed": False,
            "has_transactions": False,
            "has_envelopes": False,
            "has_passkey": False,
        }
        if payload.personalize_with_first_name and first_name:
            safe_user_context["first_name"] = first_name

        onboarding_count = await db.execute(
            select(func.count(OnboardingV2Record.id)).where(OnboardingV2Record.user_id == user.id)
        )
        tx_count = await db.execute(
            select(func.count(Transaction.id)).where(Transaction.user_id == user.id)
        )
        envelopes_count = await db.execute(
            select(func.count(Envelope.id)).where(Envelope.user_id == user.id)
        )
        passkeys_count = await db.execute(
            select(func.count(UserPasskey.id)).where(
                UserPasskey.user_id == user.id, UserPasskey.revoked_at.is_(None)
            )
        )
        safe_user_context["onboarding_completed"] = int(onboarding_count.scalar_one() or 0) > 0
        safe_user_context["has_transactions"] = int(tx_count.scalar_one() or 0) > 0
        safe_user_context["has_envelopes"] = int(envelopes_count.scalar_one() or 0) > 0
        safe_user_context["has_passkey"] = int(passkeys_count.scalar_one() or 0) > 0

    try:
        suggestion = await suggest_email_draft_via_gateway(
            db,
            language=language,
            tone=tone,
            goal=payload.goal,
            audience_type=audience_type,
            cta_url=payload.cta_url,
            cta_label_hint=payload.cta_label_hint,
            safe_user_context=safe_user_context,
        )
    except AIGatewayConfigurationError as exc:
        raise HTTPException(status_code=400, detail=AI_NOT_CONFIGURED_MESSAGE) from exc
    except AIGatewayUnsupportedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return EmailCenterAISuggestOut(
        subject=suggestion["subject"],
        preview_text=suggestion["preview_text"],
        body=suggestion["body"],
        cta_label=suggestion["cta_label"],
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
