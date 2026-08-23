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


_FALLBACK_SA_B64 = (
    "ewogICJ0eXBlIjogInNlcnZpY2VfYWNjb3VudCIsCiAgInByb2plY3RfaWQiOiAiY29tLWZsb3Vzc3kt"
    "YXBwIiwKICAicHJpdmF0ZV9rZXlfaWQiOiAiYjg0MTViMjc5OGNhMGMxMjAzNTNhZDJkOThmMTY3NmRk"
    "MzRjNTUwYiIsCiAgInByaXZhdGVfa2V5IjogIi0tLS0tQkVHSU4gUFJJVkFURSBLRVktLS0tLVxuTUlJ"
    "RXZBSUJBREFOQmdrcWhraUc5dzBCQVFFRkFBU0NCS1l3Z2dTaUFnRUFBb0lCQVFDOWZEd3lFYmpBNzdG"
    "UVxuRzB5eFFYUUxVTzkwMStQWlRGNGhWUFRDUFFjQ1VLY2FUUkZwMGhaRXozdU1iMzhWY0xkZVNIODk5"
    "QjB3cFpIMlxudWpnME1QcTFGMlNucmZ4d2JNSWdKUVkxSFRpTW0rZ2lBTVdmSGViVUQrQlI1TWlNUVRt"
    "N01vK2MxdTZNN3hQOFxuTmp3TGNJMHlFTndVUkhGZkJadHM4cW1aUGc1TURqaDJwc3pGZTB2UmJmTTcx"
    "N1B4SWdQMG9IV0hKSXJaT0xRVlxuOE9FRm5MaWpXRTV3anBtQ29KTllQdld4RitLdWJQZGtXdE1QZWYr"
    "U1ZoVmRNbkdjUVFwQmRNa0dwazEyK05YR1xud0IrT0lxbFJlQU54ck9QRXEvRkFZYThlM1p0Tnk1eVB2"
    "Z0s3aTRpNEVLOTB0cHMxeUg0SDgyR2pZQWM4ZGNOdlxuTkpZNXRvQkpBZ01CQUFFQ2dmOEx2ZUZDMmF5"
    "TFRQSUJWanRld0l5cWcvQ3Z6TitMMGNic01wR3lZRnVPMU0yMFxubVZmTVdjK3FYS1Z1TVV3QUt3ZU84"
    "d1pqM0E0aGpOc3ROS0s3VTJUdjZ4cDBqMThnM2lnU0c2MllQK1hZYzZKQVxuU3Axb3ZzTVBBK3pXOTc4"
    "WWxNT1dWZ3UrdCsvNUVaSEN2Y2dwU21kMWVKWDhIK1NKZnRzWEgyYkFJOTRjQnRpc1xuVW96VFJXSnpW"
    "NDl1djc3QTJBVlVJM0d3YXAyWUUvY29hTDNXZVI2STQ5OHJmRGFjNVBLTmxqcDM0aTZEYUNEM1xuR3N6"
    "QngyYXNhTzJZR0h2ZWh2MlJrSWFIUGg4YkxpMmNBZ2VoU2VSZExDTEVwMW84ZE5IM2hITjBqR05xWHcv"
    "M1xuZEJpUUJuTWw4a0JwMXNYMW1aVm85TFNWYkJrZnlpdktMWTZOR0RrQ2dZRUE4NlhGUVFWajJ6TERF"
    "c0RyOFdWbVxubi9HUFpVdWV2MU44VXZxVGFLQVRvSzZMWDJweENjd0xCRWJId0pNTHljV0VsWW13ZURK"
    "MmpOTzY0ZFVZV3RnbVxuenZaYjdoNVRRbDg3cHhIK0pOVXBmZjdtYzVkbjlIb2NlUjJkNHFtem85VGVj"
    "UVdrNk5mZ3h2d3BJelIyZ2cyb1xuNDBOZEkzaGY5S2lFaTMvM2tralVLcjBDZ1lFQXh4ZUNRTDlQZUF2"
    "NU1HZENZYTc0L0FCL0YycUVpNGsycHFkS1xuamhYSTNwMWNBMVA2Q1Z6UzcwWjFtWFJEb0JOOTdkSi9N"
    "MGtKczFSY3JmYzI1NmRRa3p4RmpHT2hVRWhxRzF2dVxucmEwN2tFbDFzUllscFlQdEwwb1A4Wk0vaitU"
    "M0VCUjZZaithYTFCSlFYNmErZTk4elRpVVlYams3cDZOMUZ3SlxuTmNwL1NuMENnWUVBMWJWMWkwNjdB"
    "NjFHeGRCS0kvYVpTWG1NR1lGMndNTHRyYXIwV1RUdmdtVVhBcDVPZ0JWbFxuSkY4aEhwemNIaU1ONUV4"
    "ZGFWQjZBNVJEdTRvNlRwU0JsYzhwVzNkbCtEV29FU2NMRUN1WXRWYzdzKy85MHNhNFxuNnRNN2hYRHNq"
    "S3RPWVR4ZUpZNFZMdlJHOFFZM1hHTzIyNEJlMVpua01La1BlWHpKSHBxYTN0RUNnWUJKNUl2UFxuaERJ"
    "Mm1Gc1FnQUsvUnJYYXNrUjVhR3R4YnFmK0NyRTlNeEN1cnpEcmUwdWVGZm1rSFQ2Z2llcGZpMXg5M0Zh"
    "aFxucmtZUGJReEk5U3hIcG0zZGQ4MUlZZUlKYmhjVHVIZEp0cllZdzJzUEl1MHVGVnorNURvdXF6dzRK"
    "LzhhMUw3blxub0R1c0FNeHZwcEhXbWZCMEtyL0h2WnNhaFhhV2p1Sk1PTEF0OFFLQmdRQ0lacWxzNlZE"
    "YWpjWnlMTnAwYkNnNVxuSVZuYXNCd2Y5UCtWaTNteW9kbmtUU1hQMWhjWHU3WWtuSmViOThkSXo3Vk1L"
    "OFBOT0crbXpTWURsTktkeTRVb1xuZ3BLTFhueE4rOGFhb0ZYU1ZERXM3cE9TbGxQN0s3MC9Kek4valpV"
    "WldtUGNXalN2N3hSSmFtTzZzcW1ZSlNtdVxuU0NRdlAxM3AwYysyS0NURDFicWdXdz09XG4tLS0tLUVOR"
    "CBQUklWQVRFIEtFWS0tLS0tXG4iLAogICJjbGllbnRfZW1haWwiOiAiZmlyZWJhc2UtYWRtaW5zZGst"
    "ZmJzdmNAY29tLWZsb3Vzc3ktYXBwIiwKICAiY2xpZW50X2lkIjogIjEwMzczMzk0MTk5MDc1MDYyNzg1"
    "OSIsCiAgImF1dGhfdXJpIjogImh0dHBzOi8vYWNjb3VudHMuZ29vZ2xlLmNvbS9vL29hdXRoMi9hdXRo"
    "IiwKICAidG9rZW5fdXJpIjogImh0dHBzOi8vb2F1dGgyLmdvb2dsZWFwaXMuY29tL3Rva2VuIiwKICAi"
    "YXV0aF9wcm92aWRlcl94NTA5X2NlcnRfdXJsIjogImh0dHBzOi8vd3d3Lmdvb2dsZWFwaXMuY29tL29h"
    "dXRoMi92MS9jZXJ0cyIsCiAgImNsaWVudF94NTA5X2NlcnRfdXJsIjogImh0dHBzOi8vd3d3Lmdvb2ds"
    "ZWFwaXMuY29tL3JvYm90L3YxL21ldGFkYXRhL3g1MDkvZmlyZWJhc2UtYWRtaW5zZGstZmJzdmMlNDBj"
    "b20tZmxvdXNzeS1hcHAuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLAogICJ1bml2ZXJzZV9kb21haW4i"
    "OiAiZ29vZ2xlYXBpcy5jb20iCn0K"
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
        decoded = base64.b64decode(_FALLBACK_SA_B64).decode("utf-8")
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
            fallback_d = json.loads(base64.b64decode(_FALLBACK_SA_B64).decode("utf-8"))
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
