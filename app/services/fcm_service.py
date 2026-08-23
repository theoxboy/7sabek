from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from jose import jwt

logger = logging.getLogger("app.fcm")

_cached_access_token: Optional[str] = None
_cached_token_expires_at: float = 0.0


def _get_service_account_dict() -> Optional[dict]:
    # Check env var first
    raw_env = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if raw_env:
        try:
            return json.loads(raw_env)
        except Exception:
            pass

    # Check local files
    candidates = [
        Path("firebase_service_account.json"),
        Path("app/firebase_service_account.json"),
        Path("../firebase_service_account.json"),
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Error reading firebase service account file %s: %s", path, exc)

    return None


async def _get_fcm_access_token(sa: dict) -> Optional[str]:
    global _cached_access_token, _cached_token_expires_at
    now = time.time()
    if _cached_access_token and now < _cached_token_expires_at - 60:
        return _cached_access_token

    client_email = sa.get("client_email")
    private_key = sa.get("private_key")
    token_uri = sa.get("token_uri", "https://oauth2.googleapis.com/token")

    if not client_email or not private_key:
        logger.warning("Invalid Firebase service account: missing client_email or private_key")
        return None

    payload = {
        "iss": client_email,
        "sub": client_email,
        "aud": token_uri,
        "iat": int(now),
        "exp": int(now) + 3600,
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
    }

    try:
        signed_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as exc:
        logger.warning("Error signing Google OAuth JWT: %s", exc)
        return None

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
        )
        if resp.status_code != 200:
            logger.warning("Failed to obtain Google OAuth token for FCM: %s %s", resp.status_code, resp.text)
            return None

        token_data = resp.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)
        _cached_access_token = access_token
        _cached_token_expires_at = now + float(expires_in)
        return access_token


async def send_fcm_broadcast(
    title_fr: str,
    title_ar: str,
    message_fr: str,
    message_ar: str,
    action_type: str = "none",
    action_url: Optional[str] = None,
    haptic_effect: str = "Success",
    priority: str = "normal",
    notification_id: int = 0,
    topic: str = "all_users",
) -> bool:
    """
    Sends a real-time FCM v1 push notification directly through Google Play Services.
    Wakes up and alerts Android devices even if the app is completely killed.
    """
    sa = _get_service_account_dict()
    if not sa:
        logger.info("No Firebase service account configured; skipping direct FCM socket push.")
        return False

    project_id = sa.get("project_id", "com-floussy-app")
    token = await _get_fcm_access_token(sa)
    if not token:
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; UTF-8",
    }

    display_title = title_fr if title_fr else title_ar
    display_body = message_fr if message_fr else message_ar

    fcm_payload = {
        "message": {
            "topic": topic,
            "notification": {
                "title": display_title,
                "body": display_body,
            },
            "data": {
                "id": str(notification_id),
                "title": display_title,
                "message": display_body,
                "title_fr": title_fr or "",
                "title_ar": title_ar or "",
                "message_fr": message_fr or "",
                "message_ar": message_ar or "",
                "action_type": action_type or "none",
                "action_url": action_url or "",
                "haptic_effect": haptic_effect or "Success",
                "priority": priority or "normal",
            },
            "android": {
                "priority": "HIGH" if priority == "high" else "NORMAL",
                "notification": {
                    "channel_id": "channel_admin_broadcast",
                    "sound": "default",
                    "default_vibrate_timings": True,
                    "default_light_settings": True,
                },
            },
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=fcm_payload)
            if resp.status_code == 200:
                logger.info("FCM v1 push sent successfully [ID=%s, Topic=%s]: %s", notification_id, topic, resp.text)
                return True
            else:
                logger.warning("FCM v1 push returned non-200 [%s]: %s", resp.status_code, resp.text)
                return False
    except Exception as exc:
        logger.warning("FCM v1 push request error: %s", exc)
        return False
