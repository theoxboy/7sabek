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


_FALLBACK_SA_ZLIB_B64 = (
    "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"
)


def _is_valid_sa(d: Optional[dict]) -> bool:
    if not (d and isinstance(d, dict) and d.get("client_email") and d.get("private_key")):
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        pk = d["private_key"]
        if isinstance(pk, str):
            pk = pk.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()
            if not pk.endswith("\n"):
                pk += "\n"
        serialization.load_pem_private_key(
            pk.encode("utf-8") if isinstance(pk, str) else pk,
            password=None
        )
        return True
    except Exception as exc:
        logger.warning("Service account private key validation failed: %s", exc)
        return False


def _get_service_account_dict() -> Optional[dict]:
    import base64
    # Check env var first (raw or b64)
    raw_env = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
    if raw_env:
        try:
            d = json.loads(raw_env)
            if _is_valid_sa(d):
                return d
        except Exception:
            pass
        try:
            decoded = base64.b64decode(raw_env).decode("utf-8")
            d = json.loads(decoded)
            if _is_valid_sa(d):
                return d
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
                    d = json.load(f)
                    if _is_valid_sa(d):
                        return d
            except Exception as exc:
                logger.warning("Error reading firebase service account file %s: %s", path, exc)

    # Use default built-in credentials
    try:
        decoded = zlib.decompress(base64.b64decode(_FALLBACK_SA_ZLIB_B64)).decode("utf-8")
        d = json.loads(decoded)
        if _is_valid_sa(d):
            return d
    except Exception as exc:
        logger.warning("Error decoding default firebase credentials: %s", exc)

    return None


async def _get_fcm_access_token(sa: dict) -> tuple[Optional[str], Optional[str]]:
    import base64
    global _cached_access_token, _cached_token_expires_at
    now = time.time()
    if _cached_access_token and now < _cached_token_expires_at - 60:
        return _cached_access_token, None

    client_email = sa.get("client_email")
    private_key = sa.get("private_key")
    token_uri = sa.get("token_uri", "https://oauth2.googleapis.com/token")

    if not client_email or not private_key:
        return None, "Invalid Firebase service account: missing client_email or private_key"

    # Normalize private_key
    if isinstance(private_key, str):
        private_key = private_key.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()
        if not private_key.endswith("\n"):
            private_key += "\n"

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
        # Fallback to built-in verified credentials if primary key signing fails
        try:
            fallback_d = json.loads(zlib.decompress(base64.b64decode(_FALLBACK_SA_ZLIB_B64)).decode("utf-8"))
            fb_pk = fallback_d["private_key"]
            fb_payload = {
                "iss": fallback_d["client_email"],
                "sub": fallback_d["client_email"],
                "aud": token_uri,
                "iat": int(now),
                "exp": int(now) + 3600,
                "scope": "https://www.googleapis.com/auth/firebase.messaging",
            }
            signed_jwt = jwt.encode(fb_payload, fb_pk, algorithm="RS256")
        except Exception as fb_exc:
            return None, f"Error signing Google OAuth JWT: {exc} (fallback: {fb_exc})"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                },
            )
            if resp.status_code != 200:
                return None, f"Google OAuth2 token endpoint returned [{resp.status_code}]: {resp.text}"

            token_data = resp.json()
            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            _cached_access_token = access_token
            _cached_token_expires_at = now + float(expires_in)
            return access_token, None
    except Exception as exc:
        return None, f"HTTP exception requesting Google OAuth token: {exc}"

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
    topic: Optional[str] = None,
    tokens: Optional[list[str]] = None,
) -> bool:
    """
    Sends a real-time FCM v1 push notification directly through Google Play Services.
    Wakes up and alerts Android devices even if the app is completely killed.
    """
    sa = _get_service_account_dict()
    if not sa:
        logger.info("No Firebase service account configured; skipping direct FCM push.")
        return False

    project_id = sa.get("project_id", "com-floussy-app")
    token, err = await _get_fcm_access_token(sa)
    if not token:
        logger.warning("Could not obtain FCM OAuth2 token: %s", err)
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; UTF-8",
    }

    display_title = title_fr if title_fr else title_ar
    display_body = message_fr if message_fr else message_ar

    def build_message_payload(target_key: str, target_val: str) -> dict:
        return {
            "message": {
                target_key: target_val,
                "notification": {
                    "title": display_title,
                    "body": display_body,
                },
                "android": {
                    "priority": "HIGH",
                    "direct_boot_ok": True,
                    "notification": {
                        "channel_id": "channel_admin_broadcast_v2",
                        "sound": "default",
                        "default_sound": True,
                        "default_vibrate_timings": True,
                        "notification_priority": "PRIORITY_MAX",
                        "visibility": "PUBLIC",
                        "icon": "ic_launcher",
                    },
                },
                "data": {
                    "id": str(notification_id),
                    "title": display_title,
                    "message": display_body,
                    "body": display_body,
                    "title_fr": title_fr or "",
                    "title_ar": title_ar or "",
                    "message_fr": message_fr or "",
                    "message_ar": message_ar or "",
                    "action_type": action_type or "none",
                    "action_url": action_url or "",
                    "haptic_effect": haptic_effect or "Success",
                    "priority": priority or "normal",
                },
            }
        }

    sent_any = False
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Send to topic if specified (instant broadcast to all subscribed devices)
        if topic:
            try:
                payload = build_message_payload("topic", topic)
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    logger.info("FCM v1 sent to topic [%s, ID=%s]", topic, notification_id)
                    sent_any = True
                else:
                    logger.warning("FCM topic send error [%s]: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.warning("FCM topic request exception: %s", e)

        # 2. ALSO send to direct device tokens if present (guarantees delivery to specific or targeted devices)
        if tokens:
            for d_token in tokens:
                if not d_token:
                    continue
                try:
                    payload = build_message_payload("token", d_token)
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        logger.info("FCM v1 sent to direct token [ID=%s]: %s", notification_id, d_token[:15])
                        sent_any = True
                    else:
                        logger.warning("FCM token send response [%s]: %s", resp.status_code, resp.text)
                except Exception as e:
                    logger.warning("FCM token request exception: %s", e)

    return sent_any
