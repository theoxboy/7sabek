from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.recaptcha")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
ALLOWED_HOSTNAMES = {"7sabek.ma", "www.7sabek.ma", "localhost", "127.0.0.1"}


def _is_local_environment(environment: str) -> bool:
    env = (environment or "").strip().lower()
    return env in {"local", "development", "dev", "test"}


async def verify_recaptcha_token(
    token: str,
    remote_ip: str | None = None,
) -> bool:
    settings = get_settings()
    if not settings.recaptcha_enabled:
        return _is_local_environment(settings.environment)

    secret = (settings.recaptcha_secret_key or "").strip()
    if not secret:
        logger.warning("reCAPTCHA enabled but RECAPTCHA_SECRET_KEY is missing")
        return False

    response_payload = {
        "secret": secret,
        "response": token,
    }
    if remote_ip:
        response_payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(RECAPTCHA_VERIFY_URL, data=response_payload)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("reCAPTCHA verification request failed")
        return False

    if not bool(payload.get("success")):
        return False

    hostname = payload.get("hostname")
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip().lower() in ALLOWED_HOSTNAMES

    return True
