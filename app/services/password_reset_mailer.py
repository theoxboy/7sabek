from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.password_reset_mailer")


def _email_domain(value: str) -> str:
    if "@" not in value:
        return "unknown"
    return value.rsplit("@", 1)[-1].lower()


def _build_password_reset_email(locale: str, reset_link: str) -> tuple[str, str]:
    lang = (locale or "").strip().lower()
    if lang.startswith("ar"):
        subject = "إعادة تعيين كلمة السر"
        body = (
            "توصلنا بطلب إعادة تعيين كلمة السر ديالك.\n\n"
            "استعمل هاد الرابط:\n"
            f"{reset_link}\n\n"
            "إلى ما طلبتيش هاد العملية، تجاهل هاد الرسالة."
        )
        return subject, body
    if lang.startswith("en"):
        subject = "Reset your password"
        body = (
            "We received a password reset request for your account.\n\n"
            "Use this link:\n"
            f"{reset_link}\n\n"
            "If this was not you, you can ignore this email."
        )
        return subject, body
    subject = "Réinitialisation du mot de passe"
    body = (
        "Nous avons reçu une demande de réinitialisation de mot de passe.\n\n"
        "Utilise ce lien :\n"
        f"{reset_link}\n\n"
        "Si tu n'es pas à l'origine de cette demande, ignore cet email."
    )
    return subject, body


async def send_password_reset_email(
    *,
    to_email: str,
    reset_link: str,
    locale: str = "fr",
) -> bool:
    settings = get_settings()
    subject, text = _build_password_reset_email(locale, reset_link)
    provider = (settings.mail_provider or "log").strip().lower()

    recipient_domain = _email_domain(to_email)
    sender_domain = _email_domain(settings.mail_from)

    if provider in {"", "log"}:
        logger.info(
            "Password reset email simulated provider=log from_domain=%s to_domain=%s",
            sender_domain,
            recipient_domain,
        )
        return False

    if provider != "mailtrap":
        logger.warning("Unsupported mail provider '%s'. Falling back to log mode.", provider)
        logger.info(
            "Password reset email simulated provider=log from_domain=%s to_domain=%s",
            sender_domain,
            recipient_domain,
        )
        return False

    api_token = (settings.mailtrap_api_token or "").strip()
    if not api_token:
        logger.warning("MAILTRAP_API_TOKEN is missing. Falling back to log mode.")
        logger.info(
            "Password reset email simulated provider=log from_domain=%s to_domain=%s",
            sender_domain,
            recipient_domain,
        )
        return False

    payload = {
        "from": {"email": settings.mail_from, "name": "7sabek"},
        "to": [{"email": to_email}],
        "subject": subject,
        "text": text,
        "category": "Password Reset",
    }
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    retries = max(int(settings.password_reset_delivery_retries), 1)
    last_error: Exception | None = None
    api_host = urlparse(settings.mailtrap_api_base).netloc or "unknown"
    logger.info(
        "Password reset email send start provider=mailtrap api_host=%s from_domain=%s to_domain=%s",
        api_host,
        sender_domain,
        recipient_domain,
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, retries + 1):
            try:
                response = await client.post(
                    settings.mailtrap_api_base,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                logger.info(
                    "Password reset email sent provider=mailtrap api_host=%s to_domain=%s attempt=%s",
                    api_host,
                    recipient_domain,
                    attempt,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Password reset email send failed provider=mailtrap api_host=%s "
                    "to_domain=%s attempt=%s/%s error_type=%s error=%s",
                    api_host,
                    recipient_domain,
                    attempt,
                    retries,
                    type(exc).__name__,
                    exc,
                )
                if attempt < retries:
                    await asyncio.sleep(0.35 * attempt)
    assert last_error is not None
    raise last_error
