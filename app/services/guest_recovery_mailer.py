"""
Email a "Mode Découverte" guest their recovery code, on request.

This is not a claim: no password, no account is created. It only puts the code
the guest already holds into a durable, searchable place (their inbox).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.guest_recovery_mailer")


def _domain(value: str) -> str:
    return value.rsplit("@", 1)[-1].lower() if "@" in value else "unknown"


def _build_email(locale: str, code: str, login_url: str) -> tuple[str, str]:
    lang = (locale or "").strip().lower()
    grouped = f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
    if lang.startswith("ar"):
        subject = "كود الاسترجاع ديالك ف 7sabek"
        body = (
            "هاد هو كود الاسترجاع ديال الميزانية ديالك:\n\n"
            f"    {grouped}\n\n"
            "خبّيه. كيرجّع ليك الميزانية ف أي تيليفون، بلا حساب:\n"
            f"{login_url}\n\n"
            "إلا ما طلبتيش هاد الرسالة، تجاهلها."
        )
        return subject, body
    if lang.startswith("en"):
        subject = "Your 7sabek recovery code"
        body = (
            "Here is your budget's recovery code:\n\n"
            f"    {grouped}\n\n"
            "Keep it. It brings your budget back on any device, no account:\n"
            f"{login_url}\n\n"
            "If you didn't ask for this email, ignore it."
        )
        return subject, body
    subject = "Ton code de reprise 7sabek"
    body = (
        "Voici le code de reprise de ton budget :\n\n"
        f"    {grouped}\n\n"
        "Garde-le. Il ramène ton budget sur n'importe quel appareil, sans compte :\n"
        f"{login_url}\n\n"
        "Si tu n'es pas à l'origine de cette demande, ignore cet email."
    )
    return subject, body


async def send_guest_recovery_email(
    *, to_email: str, code: str, login_url: str, locale: str = "fr"
) -> bool:
    settings = get_settings()
    subject, text = _build_email(locale, code, login_url)
    provider = (settings.mail_provider or "log").strip().lower()

    if provider != "mailtrap":
        logger.info(
            "Guest recovery email simulated provider=%s to_domain=%s", provider, _domain(to_email)
        )
        return False

    api_token = (settings.mailtrap_api_token or "").strip()
    if not api_token:
        logger.warning("MAILTRAP_API_TOKEN missing — guest recovery email simulated.")
        return False

    payload = {
        "from": {"email": settings.mail_from, "name": "7sabek"},
        "to": [{"email": to_email}],
        "subject": subject,
        "text": text,
        "category": "Guest Recovery Code",
    }
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.mailtrap_api_base, json=payload, headers=headers)
            resp.raise_for_status()
        logger.info("Guest recovery email sent to_domain=%s", _domain(to_email))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Guest recovery email failed to_domain=%s error=%s", _domain(to_email), exc)
        return False
