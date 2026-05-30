from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import hashlib
import hmac
import base64
import json

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_delivery import EmailDelivery
from app.models.email_campaign import EmailCampaign
from app.models.email_design_settings import EmailDesignSettings
from app.models.email_template import EmailTemplate
from app.models.email_preference import EmailPreference
from app.models.email_unsubscribe import EmailUnsubscribe
from app.models.email_suppression import EmailSuppression
from app.models.registration_lead import RegistrationLead
from app.models.envelope import Envelope
from app.models.onboarding_v2_record import OnboardingV2Record
from app.models.transaction import Transaction
from app.models.user import User

logger = logging.getLogger("app.email_center")

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SIMPLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_TEMPLATE_LANGUAGES = {"darija", "fr", "en"}
ALLOWED_AUDIENCE_TYPES = {
    "all_users",
    "incomplete_onboarding",
    "no_transactions",
    "no_envelopes",
    "by_language",
    "salary_today",
    "salary_tomorrow",
    "registration_leads_email_captured",
}
ALLOWED_CAMPAIGN_LANGUAGE_MODES = {"auto", "darija", "fr", "en"}
ALLOWED_CAMPAIGN_STATUS = {"draft", "ready", "queued", "archived"}
ALLOWED_TEMPLATE_CATEGORIES = {
    "welcome",
    "onboarding_reminder",
    "salary_reminder",
    "first_transaction",
    "envelope_setup",
    "passkey_reminder",
    "monthly_checkin",
    "product_update",
    "maintenance",
    "registration_reminder",
    "custom",
}
UNSUBSCRIBE_ALLOWED_CATEGORIES = {"salary_reminders", "tips", "product_updates", "marketing"}
UNSUBSCRIBE_BLOCKED_CATEGORIES = {"security", "password_reset", "account_deletion", "transactional_critical"}
SUPPRESSION_REASONS = {"unsubscribed", "bounced", "invalid_email", "blocked_by_admin", "deleted_user", "test_account", "complaint", "other"}
SUPPRESSION_SOURCES = {"manual", "unsubscribe", "provider_bounce", "system", "import"}


class EmailCenterSendTestError(Exception):
    def __init__(self, message: str, *, error_type: str, http_status: int, step: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.http_status = http_status
        self.step = step


def _send_test_safe_runtime_context(app_settings, provider: str) -> Dict[str, Any]:
    return {
        "email_center_mode": (app_settings.email_center_mode or "").strip().lower(),
        "email_center_enabled": bool(app_settings.email_center_enabled),
        "kill_switch": bool(app_settings.email_center_kill_switch),
        "provider": provider,
        "mail_from_configured": bool((app_settings.mail_from or "").strip()),
        "token_configured": bool((app_settings.mailtrap_api_token or "").strip()),
        "api_base_configured": bool((app_settings.mailtrap_api_base or "").strip()),
        "test_recipient_configured": bool((app_settings.email_center_test_recipient_email or "").strip()),
    }


def _safe_color(value: str, fallback: str) -> str:
    if HEX_COLOR_RE.match((value or "").strip()):
        return value.strip()
    return fallback


def _safe_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value.strip()
    return ""


def _escape_text_to_html(value: str) -> str:
    return html.escape((value or "").strip()).replace("\n", "<br/>")


async def get_or_create_design_settings(db: AsyncSession) -> EmailDesignSettings:
    result = await db.execute(
        select(EmailDesignSettings).order_by(EmailDesignSettings.id.desc()).limit(1)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings
    item = EmailDesignSettings(
        brand_name="7sabek",
        logo_url="",
        primary_color="#0f172a",
        button_color="#0f172a",
        footer_text="Merci d'utiliser 7sabek.",
        support_email="",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


def render_email_html(
    *,
    design: EmailDesignSettings,
    subject: str,
    body: str,
    cta_label: str,
    cta_url: str,
    manage_preferences_url: str = "",
    unsubscribe_url: str = "",
) -> Tuple[str, str]:
    brand_name = html.escape(design.brand_name)
    safe_subject = html.escape(subject.strip())
    safe_body_html = _escape_text_to_html(body)
    safe_body_text = body.strip()
    safe_footer = html.escape(design.footer_text)
    safe_support = html.escape(design.support_email)
    safe_logo = _safe_url(design.logo_url)
    safe_cta_label = html.escape((cta_label or "").strip())
    safe_cta_url = _safe_url(cta_url)
    safe_preferences_url = _safe_url(manage_preferences_url)
    safe_unsubscribe_url = _safe_url(unsubscribe_url)
    primary_color = _safe_color(design.primary_color, "#0f172a")
    button_color = _safe_color(design.button_color, "#0f172a")

    logo_html = (
        f'<img src="{html.escape(safe_logo)}" alt="{brand_name}" '
        'style="max-height:48px;max-width:180px;display:block;margin-bottom:16px;" />'
        if safe_logo
        else f'<h2 style="margin:0 0 16px 0;color:{primary_color};">{brand_name}</h2>'
    )
    cta_html = (
        f'<p style="margin-top:24px;"><a href="{html.escape(safe_cta_url)}" '
        f'style="display:inline-block;background:{button_color};color:#ffffff;'
        'text-decoration:none;padding:10px 16px;border-radius:8px;">'
        f"{safe_cta_label or 'Open'}</a></p>"
        if safe_cta_url
        else ""
    )
    footer_links: List[str] = []
    if safe_preferences_url:
        footer_links.append(
            '<a href="{0}" style="color:#64748b;text-decoration:underline;">Manage preferences</a>'.format(
                html.escape(safe_preferences_url)
            )
        )
    if safe_unsubscribe_url:
        footer_links.append(
            '<a href="{0}" style="color:#64748b;text-decoration:underline;">Unsubscribe</a>'.format(
                html.escape(safe_unsubscribe_url)
            )
        )
    footer_links_html = ""
    if footer_links:
        footer_links_html = '<p style="margin:8px 0 0 0;color:#64748b;font-size:12px;">{0}</p>'.format(" | ".join(footer_links))

    html_body = (
        "<!doctype html><html><body style=\"font-family:Arial,sans-serif;background:#f8fafc;"
        "padding:24px;\">"
        "<div style=\"max-width:620px;margin:0 auto;background:#ffffff;border-radius:12px;"
        "padding:24px;border:1px solid #e2e8f0;\">"
        f"{logo_html}"
        f"<h1 style=\"margin:0 0 12px 0;color:{primary_color};font-size:22px;\">{safe_subject}</h1>"
        f"<p style=\"margin:0;color:#1e293b;line-height:1.6;\">{safe_body_html}</p>"
        f"{cta_html}"
        f"<hr style=\"margin:24px 0;border:none;border-top:1px solid #e2e8f0;\"/>"
        f"<p style=\"margin:0;color:#64748b;font-size:12px;\">{safe_footer}</p>"
        f"<p style=\"margin:8px 0 0 0;color:#64748b;font-size:12px;\">{safe_support}</p>"
        f"{footer_links_html}"
        "</div></body></html>"
    )
    return html_body, safe_body_text


async def _create_delivery(
    db: AsyncSession,
    *,
    email: str,
    original_recipient_email: Optional[str],
    recipient_user_id=None,
    subject: str,
    language: str,
    body_html: str,
    body_text: str,
    provider: str,
    created_by_admin_id,
    note: Optional[str] = None,
    status: str = "pending",
    error_message: Optional[str] = None,
    campaign_id=None,
    category: Optional[str] = None,
    queued_at: Optional[datetime] = None,
) -> EmailDelivery:
    delivery = EmailDelivery(
        email=email,
        original_recipient_email=original_recipient_email,
        recipient_user_id=recipient_user_id,
        subject=subject,
        language=language,
        body_html=body_html,
        body_text=body_text,
        status=status,
        provider=provider,
        note=note,
        campaign_id=campaign_id,
        category=category,
        queued_at=queued_at,
        created_by_admin_id=created_by_admin_id,
        error_message=error_message,
        failed_at=datetime.now(timezone.utc) if status in {"failed", "skipped"} else None,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


async def send_test_email(
    db: AsyncSession,
    *,
    admin_user: User,
    to_email: str,
    language: str,
    subject: str,
    body: str,
    cta_label: str,
    cta_url: str,
) -> EmailDelivery:
    app_settings = get_settings()
    provider = (app_settings.mail_provider or "mailtrap").strip().lower()
    mode = (app_settings.email_center_mode or "test_only").strip().lower()
    test_recipient = (app_settings.email_center_test_recipient_email or "").strip().lower()
    normalized_to = to_email.strip().lower()
    safe_ctx = _send_test_safe_runtime_context(app_settings, provider=provider)

    async def _mark_failed_delivery(
        *,
        delivery: EmailDelivery,
        safe_error_message: str,
        step: str,
        user_message: str,
        error_type: str,
        http_status: int,
    ) -> None:
        delivery.status = "failed"
        delivery.error_message = safe_error_message[:500]
        delivery.failed_at = datetime.now(timezone.utc)
        try:
            await db.commit()
            await db.refresh(delivery)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "event=email_center_send_test_failed exception_type=%s exception_message=%s step=%s "
                "email_center_mode=%s email_center_enabled=%s kill_switch=%s provider=%s "
                "mail_from_configured=%s token_configured=%s api_base_configured=%s "
                "test_recipient_configured=%s",
                type(exc).__name__,
                str(exc),
                "save_failed_delivery",
                safe_ctx["email_center_mode"],
                safe_ctx["email_center_enabled"],
                safe_ctx["kill_switch"],
                safe_ctx["provider"],
                safe_ctx["mail_from_configured"],
                safe_ctx["token_configured"],
                safe_ctx["api_base_configured"],
                safe_ctx["test_recipient_configured"],
            )
            raise EmailCenterSendTestError(
                "Failed to save email delivery status.",
                error_type="db_error",
                http_status=500,
                step="save_failed_delivery",
            ) from exc
        raise EmailCenterSendTestError(
            user_message,
            error_type=error_type,
            http_status=http_status,
            step=step,
        )

    if mode == "test_only":
        if not test_recipient:
            raise ValueError("EMAIL_CENTER_TEST_RECIPIENT_EMAIL is required.")
        if normalized_to != test_recipient:
            raise ValueError("test recipient must match EMAIL_CENTER_TEST_RECIPIENT_EMAIL.")

    design = await get_or_create_design_settings(db)
    body_html, body_text = render_email_html(
        design=design,
        subject=subject,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )

    if app_settings.email_center_kill_switch:
        try:
            return await _create_delivery(
                db,
                email=normalized_to,
                original_recipient_email=normalized_to,
                subject=subject.strip(),
                language=language,
                body_html=body_html,
                body_text=body_text,
                provider=provider,
                created_by_admin_id=admin_user.id,
                status="skipped",
                error_message="Email center kill switch is active.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "event=email_center_send_test_failed exception_type=%s exception_message=%s step=%s "
                "email_center_mode=%s email_center_enabled=%s kill_switch=%s provider=%s "
                "mail_from_configured=%s token_configured=%s api_base_configured=%s "
                "test_recipient_configured=%s",
                type(exc).__name__,
                str(exc),
                "create_skipped_delivery",
                safe_ctx["email_center_mode"],
                safe_ctx["email_center_enabled"],
                safe_ctx["kill_switch"],
                safe_ctx["provider"],
                safe_ctx["mail_from_configured"],
                safe_ctx["token_configured"],
                safe_ctx["api_base_configured"],
                safe_ctx["test_recipient_configured"],
            )
            raise EmailCenterSendTestError(
                "Failed to save email delivery.",
                error_type="db_error",
                http_status=500,
                step="create_skipped_delivery",
            ) from exc

    try:
        delivery = await _create_delivery(
            db,
            email=normalized_to,
            original_recipient_email=normalized_to,
            subject=subject.strip(),
            language=language,
            body_html=body_html,
            body_text=body_text,
            provider=provider,
            created_by_admin_id=admin_user.id,
            status="pending",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "event=email_center_send_test_failed exception_type=%s exception_message=%s step=%s "
            "email_center_mode=%s email_center_enabled=%s kill_switch=%s provider=%s "
            "mail_from_configured=%s token_configured=%s api_base_configured=%s "
            "test_recipient_configured=%s",
            type(exc).__name__,
            str(exc),
            "create_pending_delivery",
            safe_ctx["email_center_mode"],
            safe_ctx["email_center_enabled"],
            safe_ctx["kill_switch"],
            safe_ctx["provider"],
            safe_ctx["mail_from_configured"],
            safe_ctx["token_configured"],
            safe_ctx["api_base_configured"],
            safe_ctx["test_recipient_configured"],
        )
        raise EmailCenterSendTestError(
            "Failed to save email delivery.",
            error_type="db_error",
            http_status=500,
            step="create_pending_delivery",
        ) from exc

    if provider != "mailtrap":
        await _mark_failed_delivery(
            delivery=delivery,
            safe_error_message="Unsupported mail provider.",
            step="validate_provider",
            user_message="Email provider failed",
            error_type="provider_error",
            http_status=502,
        )

    api_token = (app_settings.mailtrap_api_token or "").strip()
    if not api_token:
        await _mark_failed_delivery(
            delivery=delivery,
            safe_error_message="MAILTRAP_API_TOKEN is missing.",
            step="validate_provider_token",
            user_message="Email provider failed",
            error_type="provider_error",
            http_status=502,
        )

    payload: Dict[str, Any] = {
        "from": {"email": app_settings.mail_from, "name": design.brand_name or "7sabek"},
        "to": [{"email": normalized_to}],
        "subject": subject.strip(),
        "html": body_html,
        "text": body_text,
        "category": "Superadmin Test",
    }
    headers = {"Authorization": "Bearer {0}".format(api_token), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(app_settings.mailtrap_api_base, json=payload, headers=headers)
            status_code = int(response.status_code)
            if status_code not in {200, 202}:
                await _mark_failed_delivery(
                    delivery=delivery,
                    safe_error_message="Provider returned status {0}".format(status_code),
                    step="provider_http_status",
                    user_message="Email provider failed",
                    error_type="provider_error",
                    http_status=502,
                )
            data: Any = {}
            if response.content:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
        provider_message_id = None
        if isinstance(data, dict):
            message_ids = data.get("message_ids")
            if isinstance(message_ids, list) and message_ids:
                provider_message_id = str(message_ids[0] or "") or None
            if provider_message_id is None:
                provider_message_id = str(data.get("id") or "") or None
        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.provider_message_id = provider_message_id
        if delivery.error_message:
            delivery.error_message = None
        await db.commit()
        await db.refresh(delivery)
        return delivery
    except EmailCenterSendTestError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "event=email_center_send_test_failed exception_type=%s exception_message=%s step=%s "
            "email_center_mode=%s email_center_enabled=%s kill_switch=%s provider=%s "
            "mail_from_configured=%s token_configured=%s api_base_configured=%s "
            "test_recipient_configured=%s",
            type(exc).__name__,
            str(exc),
            "provider_request",
            safe_ctx["email_center_mode"],
            safe_ctx["email_center_enabled"],
            safe_ctx["kill_switch"],
            safe_ctx["provider"],
            safe_ctx["mail_from_configured"],
            safe_ctx["token_configured"],
            safe_ctx["api_base_configured"],
            safe_ctx["test_recipient_configured"],
        )
        await _mark_failed_delivery(
            delivery=delivery,
            safe_error_message="Send failed: {0}".format(type(exc).__name__),
            step="provider_request",
            user_message="Email provider failed",
            error_type="provider_error",
            http_status=502,
        )


def detect_user_email_language(user: User) -> str:
    supported = {"darija", "fr", "en"}
    candidates: List[Optional[str]] = []

    for attr in ("language", "locale", "preferred_language"):
        candidates.append(getattr(user, attr, None))

    for attr in ("settings", "profile", "preferences", "metadata"):
        container = getattr(user, attr, None)
        if isinstance(container, dict):
            candidates.append(container.get("language"))
            candidates.append(container.get("locale"))
            candidates.append(container.get("preferred_language"))

    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip().lower()
        if normalized in {"ar", "ar-ma", "darija", "ma"}:
            return "darija"
        if normalized in {"fr", "fr-fr", "fr-ma"}:
            return "fr"
        if normalized in {"en", "en-us", "en-gb"}:
            return "en"
        if normalized in supported:
            return normalized
    return "darija"


def build_user_display_name(user: User) -> str:
    first_name = (getattr(user, "first_name", None) or "").strip()
    last_name = (getattr(user, "last_name", None) or "").strip()
    full_name = " ".join(part for part in [first_name, last_name] if part)
    return full_name or user.email


async def search_users_for_email_center(
    db: AsyncSession, *, query: str, limit: int = 10
) -> List[User]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return []
    safe_limit = max(1, min(limit, 10))
    ilike_value = "%" + normalized_query + "%"
    result = await db.execute(
        select(User)
        .where(
            User.deleted_at.is_(None),
            (
                User.email.ilike(ilike_value)
                | User.first_name.ilike(ilike_value)
                | User.last_name.ilike(ilike_value)
            ),
        )
        .order_by(User.created_at.desc())
        .limit(safe_limit)
    )
    return list(result.scalars().all())


async def get_user_by_id_for_email_center(
    db: AsyncSession, *, user_id
) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None)).limit(1)
    )
    return result.scalar_one_or_none()


async def send_user_email(
    db: AsyncSession,
    *,
    admin_user: User,
    user: User,
    subject: str,
    body: str,
    cta_label: str,
    cta_url: str,
) -> EmailDelivery:
    app_settings = get_settings()
    provider = (app_settings.mail_provider or "mailtrap").strip().lower()
    mode = (app_settings.email_center_mode or "test_only").strip().lower()
    test_recipient = (app_settings.email_center_test_recipient_email or "").strip().lower()
    original_email = (user.email or "").strip().lower()
    delivery_email = original_email
    delivery_note = None

    if not app_settings.email_center_allow_user_send:
        raise ValueError("User send is disabled by EMAIL_CENTER_ALLOW_USER_SEND.")
    if mode == "test_only":
        raise ValueError("User send is disabled in test_only mode.")
    if mode not in {"superadmin_only", "production"}:
        raise ValueError("Unsupported EMAIL_CENTER_MODE for user send.")
    if mode == "superadmin_only":
        if not test_recipient:
            raise ValueError("EMAIL_CENTER_TEST_RECIPIENT_EMAIL is required in superadmin_only mode.")
        delivery_email = test_recipient
        delivery_note = "test_redirected"

    if await is_email_suppressed(db, email=original_email, category="marketing", user_id=user.id):
        raise ValueError("Recipient is suppressed for this category.")

    design = await get_or_create_design_settings(db)
    detected_language = detect_user_email_language(user)
    body_html, body_text = render_email_html(
        design=design,
        subject=subject,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )

    if app_settings.email_center_kill_switch:
        return await _create_delivery(
            db,
            email=delivery_email,
            original_recipient_email=original_email,
            recipient_user_id=user.id,
            subject=subject.strip(),
            language=detected_language,
            body_html=body_html,
            body_text=body_text,
            provider=provider,
            created_by_admin_id=admin_user.id,
            note=delivery_note,
            status="skipped",
            error_message="Email center kill switch is active.",
        )

    delivery = await _create_delivery(
        db,
        email=delivery_email,
        original_recipient_email=original_email,
        recipient_user_id=user.id,
        subject=subject.strip(),
        language=detected_language,
        body_html=body_html,
        body_text=body_text,
        provider=provider,
        created_by_admin_id=admin_user.id,
        note=delivery_note,
        status="pending",
    )

    if provider != "mailtrap":
        delivery.status = "failed"
        delivery.error_message = "Unsupported mail provider."
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery

    api_token = (app_settings.mailtrap_api_token or "").strip()
    if not api_token:
        delivery.status = "failed"
        delivery.error_message = "MAILTRAP_API_TOKEN is missing."
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery

    payload: Dict[str, Any] = {
        "from": {"email": app_settings.mail_from, "name": design.brand_name or "7sabek"},
        "to": [{"email": delivery_email}],
        "subject": subject.strip(),
        "html": body_html,
        "text": body_text,
        "category": "Superadmin User",
    }
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(app_settings.mailtrap_api_base, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json() if response.content else {}
        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.provider_message_id = str(
            data.get("message_ids", [None])[0] or data.get("id") or ""
        ) or None
        await db.commit()
        await db.refresh(delivery)
        return delivery
    except Exception as exc:  # noqa: BLE001
        delivery.status = "failed"
        delivery.error_message = f"Send failed: {type(exc).__name__}"
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery

async def get_delivery_history(
    db: AsyncSession, *, page: int, page_size: int
) -> Tuple[List[EmailDelivery], int]:
    total_result = await db.execute(select(func.count(EmailDelivery.id)))
    total = int(total_result.scalar_one() or 0)
    query = (
        select(EmailDelivery)
        .order_by(EmailDelivery.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    items = list(result.scalars().all())
    return items, total


def _normalize_template_language(value: str) -> str:
    lang = (value or "fr").strip().lower()
    return lang if lang in ALLOWED_TEMPLATE_LANGUAGES else "fr"


def _normalize_template_category(value: str) -> str:
    category = (value or "custom").strip().lower()
    return category if category in ALLOWED_TEMPLATE_CATEGORIES else "custom"


async def list_email_templates(
    db: AsyncSession,
    *,
    language: Optional[str] = None,
    category: Optional[str] = None,
    active_only: bool = False,
) -> List[EmailTemplate]:
    query = select(EmailTemplate).order_by(EmailTemplate.created_at.desc())
    if language:
        query = query.where(EmailTemplate.language == _normalize_template_language(language))
    if category:
        query = query.where(EmailTemplate.category == _normalize_template_category(category))
    if active_only:
        query = query.where(EmailTemplate.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_email_template(
    db: AsyncSession,
    *,
    admin_user: User,
    key: Optional[str],
    name: str,
    category: str,
    language: str,
    subject: str,
    preview_text: Optional[str],
    body: str,
    cta_label: Optional[str],
    cta_url: Optional[str],
    is_active: bool,
) -> EmailTemplate:
    clean_key = (key or "").strip() or None
    if clean_key:
        exists = await db.execute(select(EmailTemplate.id).where(EmailTemplate.key == clean_key))
        if exists.scalar_one_or_none() is not None:
            raise ValueError("Template key already exists")

    item = EmailTemplate(
        key=clean_key,
        name=name.strip(),
        category=_normalize_template_category(category),
        language=_normalize_template_language(language),
        subject=subject.strip(),
        preview_text=(preview_text or "").strip() or None,
        body=body.strip(),
        cta_label=(cta_label or "").strip() or None,
        cta_url=(cta_url or "").strip() or None,
        is_active=bool(is_active),
        created_by_admin_id=admin_user.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_email_template(
    db: AsyncSession,
    *,
    template: EmailTemplate,
    updates: Dict[str, Any],
) -> EmailTemplate:
    if "key" in updates:
        key_value = (updates.get("key") or "").strip() or None
        if key_value and key_value != template.key:
            exists = await db.execute(select(EmailTemplate.id).where(EmailTemplate.key == key_value))
            existing_id = exists.scalar_one_or_none()
            if existing_id is not None and existing_id != template.id:
                raise ValueError("Template key already exists")
        template.key = key_value
    if "name" in updates and updates.get("name") is not None:
        template.name = str(updates["name"]).strip()
    if "category" in updates and updates.get("category") is not None:
        template.category = _normalize_template_category(str(updates["category"]))
    if "language" in updates and updates.get("language") is not None:
        template.language = _normalize_template_language(str(updates["language"]))
    if "subject" in updates and updates.get("subject") is not None:
        template.subject = str(updates["subject"]).strip()
    if "preview_text" in updates:
        template.preview_text = (str(updates.get("preview_text") or "").strip() or None)
    if "body" in updates and updates.get("body") is not None:
        template.body = str(updates["body"]).strip()
    if "cta_label" in updates:
        template.cta_label = (str(updates.get("cta_label") or "").strip() or None)
    if "cta_url" in updates:
        template.cta_url = (str(updates.get("cta_url") or "").strip() or None)
    if "is_active" in updates and updates.get("is_active") is not None:
        template.is_active = bool(updates.get("is_active"))

    await db.commit()
    await db.refresh(template)
    return template


async def get_email_template_by_id(db: AsyncSession, template_id) -> Optional[EmailTemplate]:
    result = await db.execute(select(EmailTemplate).where(EmailTemplate.id == template_id).limit(1))
    return result.scalar_one_or_none()


async def deactivate_email_template(db: AsyncSession, template: EmailTemplate) -> EmailTemplate:
    template.is_active = False
    await db.commit()
    await db.refresh(template)
    return template


def _default_template_rows() -> List[Dict[str, str]]:
    definitions = [
        ("welcome", "Welcome"),
        ("onboarding_reminder", "Onboarding Reminder"),
        ("salary_reminder", "Salary Reminder"),
        ("first_transaction", "First Transaction"),
        ("envelope_setup", "Envelope Setup"),
        ("passkey_reminder", "Passkey Reminder"),
        ("monthly_checkin", "Monthly Check-in"),
        ("registration_reminder", "Registration Reminder"),
        ("custom", "Custom"),
    ]
    rows: List[Dict[str, str]] = []
    for category, label in definitions:
        rows.extend(
            [
                {
                    "key": "{0}_{1}".format(category, "darija"),
                    "name": "{0} (Darija)".format(label),
                    "category": category,
                    "language": "darija",
                    "subject": "كمّل حسابك فـ 7sabek" if category == "registration_reminder" else "رسالة {0}".format(label.lower().replace("_", " ")),
                    "preview_text": "كمل التسجيل ديالك غير فدقيقة." if category == "registration_reminder" else "رسالة قصيرة وواضحة.",
                    "body": "السلام {first_name} 👋\nبديتي تصاوب حسابك فـ 7sabek وباقي غير شوية باش تكمل. رجع وكمل التسجيل فدقيقة." if category == "registration_reminder" else "سلام! بغينا نذكروك بخطوة بسيطة فـ 7sabek باش تبقى متابع الأمور المالية ديالك بشكل منظم.",
                    "cta_label": "كمل التسجيل" if category == "registration_reminder" else "فتح 7sabek",
                    "cta_url": "",
                },
                {
                    "key": "{0}_{1}".format(category, "fr"),
                    "name": "{0} (FR)".format(label),
                    "category": category,
                    "language": "fr",
                    "subject": "Terminez votre compte 7sabek" if category == "registration_reminder" else "{0} - 7sabek".format(label),
                    "preview_text": "Finalisez votre inscription." if category == "registration_reminder" else "Message court et clair.",
                    "body": "Bonjour {first_name},\nvous avez commencé votre inscription. Il ne reste que quelques étapes pour terminer." if category == "registration_reminder" else "Bonjour, voici un rappel simple pour vous aider à avancer sereinement sur 7sabek.",
                    "cta_label": "Continuer l’inscription" if category == "registration_reminder" else "Ouvrir 7sabek",
                    "cta_url": "",
                },
                {
                    "key": "{0}_{1}".format(category, "en"),
                    "name": "{0} (EN)".format(label),
                    "category": category,
                    "language": "en",
                    "subject": "Finish setting up your 7sabek account" if category == "registration_reminder" else "{0} - 7sabek".format(label),
                    "preview_text": "Finish your signup in one minute." if category == "registration_reminder" else "Short and clear message.",
                    "body": "Hi {first_name},\nyou started creating your account. You can finish setup in just a minute." if category == "registration_reminder" else "Hi, here is a quick reminder to help you stay on track in 7sabek.",
                    "cta_label": "Continue signup" if category == "registration_reminder" else "Open 7sabek",
                    "cta_url": "",
                },
            ]
        )
    return rows


async def seed_default_email_templates(db: AsyncSession, *, admin_user: User) -> int:
    inserted = 0
    for row in _default_template_rows():
        existing = await db.execute(select(EmailTemplate.id).where(EmailTemplate.key == row["key"]))
        if existing.scalar_one_or_none() is not None:
            continue
        item = EmailTemplate(
            key=row["key"],
            name=row["name"],
            category=row["category"],
            language=row["language"],
            subject=row["subject"],
            preview_text=row["preview_text"],
            body=row["body"],
            cta_label=row["cta_label"],
            cta_url=row["cta_url"],
            is_active=True,
            created_by_admin_id=admin_user.id,
        )
        db.add(item)
        inserted += 1
    if inserted > 0:
        await db.commit()
    return inserted


def _is_preview_eligible_user(user: User, has_content: bool) -> Tuple[bool, str, Optional[str]]:
    email_value = (getattr(user, "email", None) or "").strip()
    if not email_value:
        return False, "missing email", "missing_email"
    if getattr(user, "deleted_at", None) is not None:
        return False, "user deleted", "user_deleted"
    status_value = (getattr(user, "status", "") or "").strip().lower()
    if status_value and status_value != "active":
        return False, "user disabled", "user_disabled"
    if not SIMPLE_EMAIL_RE.match(email_value):
        return False, "invalid email format", "invalid_email"
    if not has_content:
        return False, "content missing", "missing_content"
    return True, "matched audience", None


def _resolved_content_from_template_or_payload(
    *,
    template: Optional[EmailTemplate],
    subject: Optional[str],
    body: Optional[str],
    cta_label: Optional[str],
    cta_url: Optional[str],
) -> Dict[str, str]:
    resolved_subject = (subject or "").strip()
    resolved_body = (body or "").strip()
    resolved_cta_label = (cta_label or "").strip()
    resolved_cta_url = (cta_url or "").strip()
    if template is not None:
        resolved_subject = resolved_subject or (template.subject or "").strip()
        resolved_body = resolved_body or (template.body or "").strip()
        resolved_cta_label = resolved_cta_label or (template.cta_label or "").strip()
        resolved_cta_url = resolved_cta_url or (template.cta_url or "").strip()
    return {
        "subject": resolved_subject,
        "body": resolved_body,
        "cta_label": resolved_cta_label,
        "cta_url": resolved_cta_url,
        "preview_text": (resolved_body[:140] + "...") if len(resolved_body) > 140 else resolved_body,
        "has_content": "1" if (resolved_subject and resolved_body) else "",
    }


async def _fetch_audience_users(
    db: AsyncSession,
    *,
    audience_type: str,
    warnings: List[str],
) -> List[User]:
    base_query = select(User).where(User.deleted_at.is_(None), User.status == "active")

    if audience_type == "all_users" or audience_type == "by_language":
        result = await db.execute(base_query.order_by(User.created_at.desc()))
        return list(result.scalars().all())

    if audience_type == "incomplete_onboarding":
        subq = (
            select(func.count(OnboardingV2Record.id))
            .where(
                OnboardingV2Record.user_id == User.id,
                OnboardingV2Record.stage == "completed",
            )
            .scalar_subquery()
        )
        result = await db.execute(base_query.where(subq == 0).order_by(User.created_at.desc()))
        return list(result.scalars().all())

    if audience_type == "no_transactions":
        subq = (
            select(func.count(Transaction.id))
            .where(Transaction.user_id == User.id)
            .scalar_subquery()
        )
        result = await db.execute(base_query.where(subq == 0).order_by(User.created_at.desc()))
        return list(result.scalars().all())

    if audience_type == "no_envelopes":
        subq = (
            select(func.count(Envelope.id))
            .where(Envelope.user_id == User.id)
            .scalar_subquery()
        )
        result = await db.execute(base_query.where(subq == 0).order_by(User.created_at.desc()))
        return list(result.scalars().all())

    if audience_type in {"salary_today", "salary_tomorrow"}:
        warnings.append("Salary date field not found or not supported yet")
        return []

    warnings.append("Unsupported audience_type: {0}".format(audience_type))
    return []


async def build_recipients_preview(
    db: AsyncSession,
    *,
    audience_type: str,
    language: Optional[str],
    template_id,
    subject: Optional[str],
    body: Optional[str],
    cta_label: Optional[str],
    cta_url: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    warnings: List[str] = []
    template = None
    if template_id is not None:
        template = await get_email_template_by_id(db, template_id)
        if template is None:
            warnings.append("Template not found; falling back to manual content fields")

    content = _resolved_content_from_template_or_payload(
        template=template,
        subject=subject,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )
    has_content = bool(content["has_content"])
    if audience_type == "registration_leads_email_captured":
        query = (
            select(RegistrationLead)
            .where(
                RegistrationLead.normalized_email.is_not(None),
                RegistrationLead.converted_user_id.is_(None),
                RegistrationLead.status.in_(["partial", "email_captured"]),
            )
            .order_by(RegistrationLead.last_seen_at.desc())
        )
        lead_result = await db.execute(query)
        leads = list(lead_result.scalars().all())
        items = []
        for lead in leads:
            lead_email = (lead.email or "").strip().lower()
            if not lead_email or not SIMPLE_EMAIL_RE.match(lead_email):
                continue
            suppressed = await is_email_suppressed(
                db,
                email=lead_email,
                category="marketing",
                user_id=None,
            )
            items.append(
                {
                    "user_id": None,
                    "lead_id": lead.id,
                    "recipient_type": "registration_lead",
                    "email": lead_email,
                    "first_name": lead.first_name,
                    "last_name": lead.last_name,
                    "display_name": build_user_display_name(lead),
                    "detected_language": (lead.language or "darija").strip().lower() or "darija",
                    "eligible": (not suppressed) and has_content,
                    "reason": "incomplete_registration" if not suppressed else "suppressed",
                    "skip_reason": "suppressed" if suppressed else (None if has_content else "missing_content"),
                }
            )
        total_matched = len(items)
        safe_limit = max(1, min(int(limit or 50), 200))
        returned = items[:safe_limit]
        return {
            "audience_type": audience_type,
            "total_matched": total_matched,
            "returned_count": len(returned),
            "items": returned,
            "warnings": warnings,
        }

    users = await _fetch_audience_users(db, audience_type=audience_type, warnings=warnings)
    normalized_language = (language or "").strip().lower()
    if normalized_language and normalized_language not in ALLOWED_TEMPLATE_LANGUAGES:
        warnings.append("Unsupported language filter")
        normalized_language = ""

    items: List[Dict[str, Any]] = []
    for user in users:
        detected_language = detect_user_email_language(user)
        if audience_type == "by_language" and normalized_language and detected_language != normalized_language:
            continue
        eligible, reason, skip_reason = _is_preview_eligible_user(user, has_content)
        category_for_checks = "marketing"
        if audience_type in {"salary_today", "salary_tomorrow"}:
            category_for_checks = "salary_reminders"
        if eligible:
            is_enabled = await is_email_category_enabled(db, user, category_for_checks)
            if not is_enabled:
                eligible = False
                reason = "skipped by preferences"
                skip_reason = "preferences"
        if eligible and await is_email_suppressed(
            db,
            email=user.email,
            category=category_for_checks,
            user_id=user.id,
        ):
            eligible = False
            reason = "suppressed"
            skip_reason = "suppressed"
        if template is not None and template.language != detected_language and eligible:
            reason = "matched audience; template language fallback"
        items.append(
            {
                "user_id": user.id,
                "lead_id": None,
                "recipient_type": "user",
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "display_name": build_user_display_name(user),
                "detected_language": detected_language,
                "eligible": eligible,
                "reason": reason,
                "skip_reason": skip_reason,
            }
        )

    total_matched = len(items)
    safe_limit = max(1, min(int(limit or 50), 200))
    returned = items[:safe_limit]
    return {
        "audience_type": audience_type,
        "total_matched": total_matched,
        "returned_count": len(returned),
        "items": returned,
        "warnings": warnings,
    }


async def build_preview_user_email(
    db: AsyncSession,
    *,
    user_id,
    template_id,
    subject: Optional[str],
    body: Optional[str],
    cta_label: Optional[str],
    cta_url: Optional[str],
) -> Optional[Dict[str, str]]:
    user = await get_user_by_id_for_email_center(db, user_id=user_id)
    if user is None or not (user.email or "").strip():
        return None
    template = None
    if template_id is not None:
        template = await get_email_template_by_id(db, template_id)
    content = _resolved_content_from_template_or_payload(
        template=template,
        subject=subject,
        body=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )
    if not content["has_content"]:
        return None
    design = await get_or_create_design_settings(db)
    body_html, body_text = render_email_html(
        design=design,
        subject=content["subject"],
        body=content["body"],
        cta_label=content["cta_label"],
        cta_url=content["cta_url"],
    )
    return {
        "user_id": str(user.id),
        "email": user.email,
        "detected_language": detect_user_email_language(user),
        "subject": content["subject"],
        "preview_text": content["preview_text"],
        "body_html": body_html,
        "body_text": body_text,
        "cta_label": content["cta_label"],
        "cta_url": content["cta_url"],
    }


def validate_campaign_audience_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_AUDIENCE_TYPES:
        raise ValueError("Invalid audience_type")
    return normalized


def validate_campaign_language_mode(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_CAMPAIGN_LANGUAGE_MODES:
        raise ValueError("Invalid language_mode")
    return normalized


def validate_campaign_status(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_CAMPAIGN_STATUS:
        raise ValueError("Invalid campaign status")
    return normalized


async def list_email_campaigns(
    db: AsyncSession,
    *,
    status_filter: Optional[str],
    audience_type_filter: Optional[str],
    limit: int,
    offset: int,
) -> List[EmailCampaign]:
    query = select(EmailCampaign).where(EmailCampaign.deleted_at.is_(None)).order_by(
        EmailCampaign.created_at.desc()
    )
    if status_filter:
        query = query.where(EmailCampaign.status == status_filter)
    if audience_type_filter:
        query = query.where(EmailCampaign.audience_type == audience_type_filter)
    query = query.limit(max(1, min(limit, 200))).offset(max(0, offset))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_email_campaign_by_id(
    db: AsyncSession, *, campaign_id, include_deleted: bool = False
) -> Optional[EmailCampaign]:
    query = select(EmailCampaign).where(EmailCampaign.id == campaign_id)
    if not include_deleted:
        query = query.where(EmailCampaign.deleted_at.is_(None))
    query = query.limit(1)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_email_campaign(
    db: AsyncSession,
    *,
    admin_user: User,
    payload: Dict[str, Any],
) -> EmailCampaign:
    audience_type = validate_campaign_audience_type(str(payload.get("audience_type") or ""))
    language_mode = validate_campaign_language_mode(str(payload.get("language_mode") or "auto"))
    status_value = validate_campaign_status(str(payload.get("status") or "draft"))
    item = EmailCampaign(
        title=str(payload.get("title") or "").strip(),
        type=(str(payload.get("type") or "manual").strip() or "manual"),
        status=status_value,
        audience_type=audience_type,
        audience_filter_json=payload.get("audience_filter_json"),
        language_mode=language_mode,
        template_id=payload.get("template_id"),
        subject_by_language_json=payload.get("subject_by_language_json"),
        preview_by_language_json=payload.get("preview_by_language_json"),
        body_by_language_json=payload.get("body_by_language_json"),
        cta_label_by_language_json=payload.get("cta_label_by_language_json"),
        cta_url=(str(payload.get("cta_url") or "").strip() or None),
        design_settings_json=payload.get("design_settings_json"),
        created_by_admin_id=admin_user.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_email_campaign(
    db: AsyncSession,
    *,
    campaign: EmailCampaign,
    updates: Dict[str, Any],
) -> EmailCampaign:
    if campaign.status not in {"draft", "ready"}:
        raise ValueError("Only draft/ready campaigns can be updated")
    if "title" in updates and updates.get("title") is not None:
        campaign.title = str(updates.get("title")).strip()
    if "type" in updates and updates.get("type") is not None:
        campaign.type = str(updates.get("type")).strip() or campaign.type
    if "audience_type" in updates and updates.get("audience_type") is not None:
        campaign.audience_type = validate_campaign_audience_type(str(updates.get("audience_type")))
    if "audience_filter_json" in updates:
        campaign.audience_filter_json = updates.get("audience_filter_json")
    if "language_mode" in updates and updates.get("language_mode") is not None:
        campaign.language_mode = validate_campaign_language_mode(str(updates.get("language_mode")))
    if "template_id" in updates:
        campaign.template_id = updates.get("template_id")
    if "subject_by_language_json" in updates:
        campaign.subject_by_language_json = updates.get("subject_by_language_json")
    if "preview_by_language_json" in updates:
        campaign.preview_by_language_json = updates.get("preview_by_language_json")
    if "body_by_language_json" in updates:
        campaign.body_by_language_json = updates.get("body_by_language_json")
    if "cta_label_by_language_json" in updates:
        campaign.cta_label_by_language_json = updates.get("cta_label_by_language_json")
    if "cta_url" in updates:
        campaign.cta_url = (str(updates.get("cta_url") or "").strip() or None)
    if "design_settings_json" in updates:
        campaign.design_settings_json = updates.get("design_settings_json")
    if "estimated_recipient_count" in updates:
        campaign.estimated_recipient_count = updates.get("estimated_recipient_count")
    if "status" in updates and updates.get("status") is not None:
        campaign.status = validate_campaign_status(str(updates.get("status")))
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def soft_delete_email_campaign(db: AsyncSession, *, campaign: EmailCampaign) -> EmailCampaign:
    campaign.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def duplicate_email_campaign(
    db: AsyncSession, *, campaign: EmailCampaign, admin_user: User
) -> EmailCampaign:
    copied = EmailCampaign(
        title="Copy of {0}".format(campaign.title),
        type=campaign.type,
        status="draft",
        audience_type=campaign.audience_type,
        audience_filter_json=campaign.audience_filter_json,
        language_mode=campaign.language_mode,
        template_id=campaign.template_id,
        subject_by_language_json=campaign.subject_by_language_json,
        preview_by_language_json=campaign.preview_by_language_json,
        body_by_language_json=campaign.body_by_language_json,
        cta_label_by_language_json=campaign.cta_label_by_language_json,
        cta_url=campaign.cta_url,
        design_settings_json=campaign.design_settings_json,
        estimated_recipient_count=campaign.estimated_recipient_count,
        created_by_admin_id=admin_user.id,
    )
    db.add(copied)
    await db.commit()
    await db.refresh(copied)
    return copied


def _content_value_by_language(content: Optional[Dict[str, Any]], language: str) -> str:
    if not isinstance(content, dict):
        return ""
    value = content.get(language)
    return str(value).strip() if isinstance(value, str) else ""


def _campaign_content_for_language(
    *, campaign: EmailCampaign, language: str, template: Optional[EmailTemplate]
) -> Dict[str, str]:
    normalized_language = (language or "").strip().lower()
    if normalized_language not in ALLOWED_TEMPLATE_LANGUAGES:
        raise ValueError("Invalid language")

    if campaign.language_mode in ALLOWED_TEMPLATE_LANGUAGES and campaign.language_mode != normalized_language:
        raise ValueError("Campaign language mode is fixed to {0}".format(campaign.language_mode))

    subject = _content_value_by_language(campaign.subject_by_language_json, normalized_language)
    body = _content_value_by_language(campaign.body_by_language_json, normalized_language)
    cta_label = _content_value_by_language(campaign.cta_label_by_language_json, normalized_language)
    cta_url = (campaign.cta_url or "").strip()

    if template is not None:
        subject = subject or (template.subject or "").strip()
        body = body or (template.body or "").strip()
        cta_label = cta_label or (template.cta_label or "").strip()
        cta_url = cta_url or (template.cta_url or "").strip()

    if not subject or not body:
        raise ValueError("Campaign has no content for this language.")

    return {
        "subject": subject,
        "body": body,
        "cta_label": cta_label,
        "cta_url": cta_url,
    }


async def build_campaign_recipients_preview(
    db: AsyncSession, *, campaign: EmailCampaign, limit: int
) -> Dict[str, Any]:
    warnings: List[str] = []
    template = None
    if campaign.template_id is not None:
        template = await get_email_template_by_id(db, campaign.template_id)
        if template is None:
            warnings.append("Template not found; falling back to campaign content")

    content_language = campaign.language_mode if campaign.language_mode in ALLOWED_TEMPLATE_LANGUAGES else "fr"
    preview = await build_recipients_preview(
        db,
        audience_type=campaign.audience_type,
        language=(
            campaign.language_mode
            if campaign.language_mode in ALLOWED_TEMPLATE_LANGUAGES
            else (campaign.audience_filter_json or {}).get("language")
        ),
        template_id=campaign.template_id,
        subject=_content_value_by_language(campaign.subject_by_language_json, content_language),
        body=_content_value_by_language(campaign.body_by_language_json, content_language),
        cta_label=_content_value_by_language(campaign.cta_label_by_language_json, content_language),
        cta_url=campaign.cta_url,
        limit=limit,
    )
    if campaign.language_mode == "auto":
        langs = ["darija", "fr", "en"]
        for lang in langs:
            subject_value = _content_value_by_language(campaign.subject_by_language_json, lang)
            body_value = _content_value_by_language(campaign.body_by_language_json, lang)
            if not template and (not subject_value or not body_value):
                warnings.append("Missing content for language {0}".format(lang))
    preview["warnings"] = list(dict.fromkeys((preview.get("warnings") or []) + warnings))
    return preview


async def send_campaign_test_email(
    db: AsyncSession,
    *,
    admin_user: User,
    campaign: EmailCampaign,
    language: str,
    requested_test_email: Optional[str],
) -> EmailDelivery:
    app_settings = get_settings()
    provider = (app_settings.mail_provider or "mailtrap").strip().lower()
    configured_test_recipient = (app_settings.email_center_test_recipient_email or "").strip().lower()
    if not configured_test_recipient:
        raise ValueError("EMAIL_CENTER_TEST_RECIPIENT_EMAIL is required.")
    requested = (requested_test_email or "").strip().lower()
    if requested and requested != configured_test_recipient:
        raise ValueError("test_email must match EMAIL_CENTER_TEST_RECIPIENT_EMAIL")

    template = None
    if campaign.template_id is not None:
        template = await get_email_template_by_id(db, campaign.template_id)
    content = _campaign_content_for_language(campaign=campaign, language=language, template=template)
    # Safe placeholder replacement for test-only campaign rendering.
    body_value = content["body"].replace("{first_name}", "Test")

    design = await get_or_create_design_settings(db)
    body_html, body_text = render_email_html(
        design=design,
        subject=content["subject"],
        body=body_value,
        cta_label=content["cta_label"],
        cta_url=content["cta_url"],
    )

    note_value = "campaign_test:{0}".format(str(campaign.id))
    if app_settings.email_center_kill_switch:
        return await _create_delivery(
            db,
            email=configured_test_recipient,
            original_recipient_email="campaign_test",
            recipient_user_id=None,
            subject=content["subject"],
            language=language,
            body_html=body_html,
            body_text=body_text,
            provider=provider,
            created_by_admin_id=admin_user.id,
            note=note_value,
            status="skipped",
            error_message="Email center kill switch is active.",
        )

    delivery = await _create_delivery(
        db,
        email=configured_test_recipient,
        original_recipient_email="campaign_test",
        recipient_user_id=None,
        subject=content["subject"],
        language=language,
        body_html=body_html,
        body_text=body_text,
        provider=provider,
        created_by_admin_id=admin_user.id,
        note=note_value,
        status="pending",
    )

    if provider != "mailtrap":
        delivery.status = "failed"
        delivery.error_message = "Unsupported mail provider."
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery

    api_token = (app_settings.mailtrap_api_token or "").strip()
    if not api_token:
        delivery.status = "failed"
        delivery.error_message = "MAILTRAP_API_TOKEN is missing."
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery

    payload: Dict[str, Any] = {
        "from": {"email": app_settings.mail_from, "name": design.brand_name or "7sabek"},
        "to": [{"email": configured_test_recipient}],
        "subject": content["subject"],
        "html": body_html,
        "text": body_text,
        "category": "Campaign Test",
    }
    headers = {"Authorization": "Bearer {0}".format(api_token), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(app_settings.mailtrap_api_base, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json() if response.content else {}
        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.provider_message_id = str(
            data.get("message_ids", [None])[0] or data.get("id") or ""
        ) or None
        await db.commit()
        await db.refresh(delivery)
        return delivery
    except Exception as exc:  # noqa: BLE001
        delivery.status = "failed"
        delivery.error_message = "Send failed: {0}".format(type(exc).__name__)
        delivery.failed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(delivery)
        return delivery


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _category_pref_field(category: str) -> Optional[str]:
    mapping = {
        "salary_reminders": "salary_reminders_enabled",
        "tips": "tips_enabled",
        "product_updates": "product_updates_enabled",
        "marketing": "marketing_enabled",
        "security": "security_emails_enabled",
    }
    return mapping.get((category or "").strip().lower())


async def get_or_create_email_preferences(db: AsyncSession, user_id) -> EmailPreference:
    result = await db.execute(select(EmailPreference).where(EmailPreference.user_id == user_id).limit(1))
    item = result.scalar_one_or_none()
    if item is not None:
        return item
    item = EmailPreference(user_id=user_id)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_email_preferences(db: AsyncSession, user_id, payload: Dict[str, Any]) -> EmailPreference:
    item = await get_or_create_email_preferences(db, user_id)
    for key in [
        "salary_reminders_enabled",
        "tips_enabled",
        "product_updates_enabled",
        "marketing_enabled",
    ]:
        if key in payload and payload.get(key) is not None:
            setattr(item, key, bool(payload.get(key)))
    if payload.get("security_emails_enabled") is True:
        item.security_emails_enabled = True
    await db.commit()
    await db.refresh(item)
    return item


async def is_email_category_enabled(db: AsyncSession, user: User, category: str) -> bool:
    normalized = (category or "").strip().lower()
    if normalized in UNSUBSCRIBE_BLOCKED_CATEGORIES:
        return True
    field = _category_pref_field(normalized)
    if field is None:
        return True
    prefs = await get_or_create_email_preferences(db, user.id)
    return bool(getattr(prefs, field, True))


def generate_unsubscribe_token(email: str, category: str) -> str:
    settings = get_settings()
    payload = {
        "email": normalize_email(email),
        "category": (category or "").strip().lower(),
        "ts": int(datetime.now(timezone.utc).timestamp()),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    secret = (settings.jwt_secret or "").encode("utf-8")
    signature = hmac.new(secret, encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return "{0}.{1}".format(encoded, signature)


def validate_unsubscribe_token(token: str) -> Optional[Dict[str, str]]:
    try:
        encoded, signature = token.split(".", 1)
    except ValueError:
        return None
    secret = (get_settings().jwt_secret or "").encode("utf-8")
    expected = hmac.new(secret, encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        return None
    email = normalize_email(str(payload.get("email") or ""))
    category = (str(payload.get("category") or "")).strip().lower()
    token_ts_raw = payload.get("ts")
    try:
        token_ts = int(token_ts_raw)
    except Exception:
        return None
    ttl_days = max(1, int(get_settings().email_center_unsubscribe_token_ttl_days or 30))
    expires_at = datetime.fromtimestamp(token_ts, tz=timezone.utc) + timedelta(days=ttl_days)
    if datetime.now(timezone.utc) > expires_at:
        return None
    if not email or category not in UNSUBSCRIBE_ALLOWED_CATEGORIES:
        return None
    return {"email": email, "category": category}


async def record_unsubscribe(
    db: AsyncSession,
    *,
    email: str,
    category: str,
    token_hash: Optional[str] = None,
    user_id=None,
) -> EmailUnsubscribe:
    normalized = normalize_email(email)
    category_value = (category or "").strip().lower()
    item = EmailUnsubscribe(
        email=normalized,
        category=category_value,
        token_hash=token_hash,
        user_id=user_id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def is_email_suppressed(
    db: AsyncSession,
    *,
    email: str,
    category: Optional[str] = None,
    user_id=None,
) -> bool:
    if not get_settings().email_center_suppression_enabled:
        return False
    normalized = normalize_email(email)
    category_value = (category or "").strip().lower()
    if category_value in UNSUBSCRIBE_BLOCKED_CATEGORIES:
        return False
    query = select(EmailSuppression).where(
        EmailSuppression.email == normalized,
        EmailSuppression.is_active.is_(True),
    )
    if user_id is not None:
        query = query.where((EmailSuppression.user_id.is_(None)) | (EmailSuppression.user_id == user_id))
    if category_value:
        query = query.where((EmailSuppression.category.is_(None)) | (EmailSuppression.category == category_value))
    result = await db.execute(query.limit(1))
    return result.scalar_one_or_none() is not None


async def compute_campaign_eligible_recipients(
    db: AsyncSession,
    *,
    campaign: EmailCampaign,
    cap_plus_one: int,
) -> Dict[str, Any]:
    warnings: List[str] = []
    users = await _fetch_audience_users(db, audience_type=campaign.audience_type, warnings=warnings)
    eligible_items: List[Dict[str, Any]] = []
    skipped_count = 0

    normalized_language = ""
    if campaign.language_mode in ALLOWED_TEMPLATE_LANGUAGES:
        normalized_language = campaign.language_mode
    elif isinstance(campaign.audience_filter_json, dict):
        candidate = (campaign.audience_filter_json.get("language") or "").strip().lower()
        if candidate in ALLOWED_TEMPLATE_LANGUAGES:
            normalized_language = candidate

    for user in users:
        detected_language = detect_user_email_language(user)
        if campaign.audience_type == "by_language" and normalized_language and detected_language != normalized_language:
            continue

        eligible, _reason, _skip_reason = _is_preview_eligible_user(user, True)
        category_for_checks = "marketing"
        if campaign.audience_type in {"salary_today", "salary_tomorrow"}:
            category_for_checks = "salary_reminders"
        if eligible and not await is_email_category_enabled(db, user, category_for_checks):
            eligible = False
        if eligible and await is_email_suppressed(
            db,
            email=user.email,
            category=category_for_checks,
            user_id=user.id,
        ):
            eligible = False

        if not eligible:
            skipped_count += 1
            continue

        eligible_items.append(
            {
                "user_id": user.id,
                "email": user.email,
                "detected_language": detected_language,
            }
        )
        if len(eligible_items) > cap_plus_one:
            break

    return {
        "eligible_items": eligible_items,
        "skipped_count": skipped_count,
        "warnings": warnings,
    }


async def add_email_suppression(
    db: AsyncSession,
    *,
    email: str,
    reason: str,
    source: Optional[str] = None,
    category: Optional[str] = None,
    user_id=None,
    created_by_admin_id=None,
) -> EmailSuppression:
    reason_value = (reason or "").strip().lower()
    source_value = (source or "").strip().lower() or None
    if reason_value not in SUPPRESSION_REASONS:
        raise ValueError("Invalid suppression reason")
    if source_value is not None and source_value not in SUPPRESSION_SOURCES:
        raise ValueError("Invalid suppression source")
    item = EmailSuppression(
        email=normalize_email(email),
        user_id=user_id,
        category=(category or "").strip().lower() or None,
        reason=reason_value,
        source=source_value,
        is_active=True,
        created_by_admin_id=created_by_admin_id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def deactivate_email_suppression(db: AsyncSession, suppression: EmailSuppression) -> EmailSuppression:
    suppression.is_active = False
    suppression.deactivated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(suppression)
    return suppression


async def list_email_suppressions(
    db: AsyncSession,
    *,
    q: Optional[str],
    reason: Optional[str],
    active_only: bool,
    limit: int,
    offset: int,
) -> Tuple[List[EmailSuppression], int]:
    query = select(EmailSuppression)
    count_query = select(func.count(EmailSuppression.id))
    if q:
        needle = "%{0}%".format(normalize_email(q))
        query = query.where(EmailSuppression.email.ilike(needle))
        count_query = count_query.where(EmailSuppression.email.ilike(needle))
    if reason:
        reason_value = (reason or "").strip().lower()
        query = query.where(EmailSuppression.reason == reason_value)
        count_query = count_query.where(EmailSuppression.reason == reason_value)
    if active_only:
        query = query.where(EmailSuppression.is_active.is_(True))
        count_query = count_query.where(EmailSuppression.is_active.is_(True))
    total = int((await db.execute(count_query)).scalar_one() or 0)
    result = await db.execute(query.order_by(EmailSuppression.created_at.desc()).limit(limit).offset(offset))
    return list(result.scalars().all()), total

async def enqueue_delivery(
    db: AsyncSession,
    *,
    email: str,
    recipient_user_id,
    subject: str,
    language: str,
    body_html: str,
    body_text: str,
    provider: str,
    created_by_admin_id,
    campaign_id=None,
    category: Optional[str] = None,
    note: Optional[str] = None,
) -> EmailDelivery:
    return await _create_delivery(
        db,
        email=email,
        original_recipient_email=email,
        recipient_user_id=recipient_user_id,
        subject=subject,
        language=language,
        body_html=body_html,
        body_text=body_text,
        provider=provider,
        created_by_admin_id=created_by_admin_id,
        campaign_id=campaign_id,
        category=category,
        queued_at=datetime.now(timezone.utc),
        note=note,
        status="pending",
    )


async def get_due_deliveries(db: AsyncSession, limit: int) -> List[EmailDelivery]:
    now = datetime.now(timezone.utc)
    query = (
        select(EmailDelivery)
        .where(
            EmailDelivery.status.in_(["pending", "retry"]),
            (EmailDelivery.next_attempt_at.is_(None)) | (EmailDelivery.next_attempt_at <= now),
        )
        .order_by(EmailDelivery.queued_at.asc().nullsfirst(), EmailDelivery.created_at.asc())
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def process_delivery_batch(db: AsyncSession, limit: int) -> Dict[str, int]:
    settings = get_settings()
    if not settings.email_center_delivery_queue_enabled:
        return {"attempted": 0, "sent": 0, "failed": 0, "retry": 0, "remaining_pending": 0}
    if settings.email_center_kill_switch:
        return {"attempted": 0, "sent": 0, "failed": 0, "retry": 0, "remaining_pending": 0}

    deliveries = await get_due_deliveries(db, limit)
    attempted = len(deliveries)
    sent = 0
    failed = 0
    retry = 0
    provider = (settings.mail_provider or "mailtrap").strip().lower()
    max_attempts = max(1, int(settings.email_center_queue_max_attempts or 3))
    retry_delay_minutes = max(1, int(settings.email_center_queue_retry_delay_minutes or 30))

    async def _mark_retry_or_failed(delivery: EmailDelivery, *, permanent: bool, status_code: Optional[int] = None) -> str:
        if status_code is not None:
            delivery.provider_status_code = str(status_code)
        if permanent or int(delivery.attempt_count or 0) >= max_attempts:
            delivery.status = "failed"
            delivery.failed_at = datetime.now(timezone.utc)
            return "failed"
        delivery.status = "retry"
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=retry_delay_minutes)
        return "retry"

    for delivery in deliveries:
        try:
            delivery.attempt_count = int(delivery.attempt_count or 0) + 1
            delivery.last_attempt_at = datetime.now(timezone.utc)
            delivery.status = "sending"
            await db.commit()

            if provider != "mailtrap":
                outcome = await _mark_retry_or_failed(delivery, permanent=True)
                if outcome == "failed":
                    failed += 1
                else:
                    retry += 1
                await db.commit()
                continue

            api_token = (settings.mailtrap_api_token or "").strip()
            if not api_token:
                outcome = await _mark_retry_or_failed(delivery, permanent=True)
                if outcome == "failed":
                    failed += 1
                else:
                    retry += 1
                await db.commit()
                continue

            payload: Dict[str, Any] = {
                "from": {"email": settings.mail_from, "name": "7sabek"},
                "to": [{"email": delivery.email}],
                "subject": delivery.subject,
                "html": delivery.body_html,
                "text": delivery.body_text,
                "category": "Campaign Queue",
            }
            headers = {"Authorization": "Bearer {0}".format(api_token), "Content-Type": "application/json"}

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(settings.mailtrap_api_base, json=payload, headers=headers)
                status_code = int(response.status_code)
                delivery.provider_status_code = str(status_code)
                if 200 <= status_code < 300:
                    data = response.json() if response.content else {}
                    delivery.status = "sent"
                    delivery.sent_at = datetime.now(timezone.utc)
                    delivery.provider_message_id = str(data.get("message_ids", [None])[0] or data.get("id") or "") or None
                    sent += 1
                elif status_code == 429 or status_code >= 500:
                    outcome = await _mark_retry_or_failed(delivery, permanent=False, status_code=status_code)
                    if outcome == "failed":
                        failed += 1
                    else:
                        retry += 1
                else:
                    outcome = await _mark_retry_or_failed(delivery, permanent=True, status_code=status_code)
                    if outcome == "failed":
                        failed += 1
                    else:
                        retry += 1
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
                outcome = await _mark_retry_or_failed(delivery, permanent=False)
                if outcome == "failed":
                    failed += 1
                else:
                    retry += 1

            await db.commit()
        except Exception:
            await db.rollback()
            failed += 1
    remaining_result = await db.execute(
        select(func.count(EmailDelivery.id)).where(EmailDelivery.status.in_(["pending", "retry"]))
    )
    remaining_pending = int(remaining_result.scalar_one() or 0)
    return {
        "attempted": attempted,
        "sent": sent,
        "failed": failed,
        "retry": retry,
        "remaining_pending": remaining_pending,
    }
