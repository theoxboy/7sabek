from __future__ import annotations

from datetime import datetime, timezone
import html
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_delivery import EmailDelivery
from app.models.email_design_settings import EmailDesignSettings
from app.models.user import User

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


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

    if mode == "test_only":
        if not test_recipient or normalized_to != test_recipient:
            raise ValueError("Test mode only allows EMAIL_CENTER_TEST_RECIPIENT_EMAIL.")

    design = await get_or_create_design_settings(db)
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

    payload: Dict[str, Any] = {
        "from": {"email": app_settings.mail_from, "name": design.brand_name or "7sabek"},
        "to": [{"email": normalized_to}],
        "subject": subject.strip(),
        "html": body_html,
        "text": body_text,
        "category": "Superadmin Test",
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
