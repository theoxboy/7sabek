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
from app.models.email_template import EmailTemplate
from app.models.user import User

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ALLOWED_TEMPLATE_LANGUAGES = {"darija", "fr", "en"}
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
    "custom",
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
                    "subject": "رسالة {0}".format(label.lower().replace("_", " ")),
                    "preview_text": "رسالة قصيرة وواضحة.",
                    "body": "سلام! بغينا نذكروك بخطوة بسيطة فـ 7sabek باش تبقى متابع الأمور المالية ديالك بشكل منظم.",
                    "cta_label": "فتح 7sabek",
                    "cta_url": "",
                },
                {
                    "key": "{0}_{1}".format(category, "fr"),
                    "name": "{0} (FR)".format(label),
                    "category": category,
                    "language": "fr",
                    "subject": "{0} - 7sabek".format(label),
                    "preview_text": "Message court et clair.",
                    "body": "Bonjour, voici un rappel simple pour vous aider à avancer sereinement sur 7sabek.",
                    "cta_label": "Ouvrir 7sabek",
                    "cta_url": "",
                },
                {
                    "key": "{0}_{1}".format(category, "en"),
                    "name": "{0} (EN)".format(label),
                    "category": category,
                    "language": "en",
                    "subject": "{0} - 7sabek".format(label),
                    "preview_text": "Short and clear message.",
                    "body": "Hi, here is a quick reminder to help you stay on track in 7sabek.",
                    "cta_label": "Open 7sabek",
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
