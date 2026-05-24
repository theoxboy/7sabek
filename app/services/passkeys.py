from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import get_client_ip
from app.models import WebAuthnChallenge


def challenge_hash(raw_challenge: str) -> str:
    return sha256(raw_challenge.encode("utf-8")).hexdigest()


def _decode_client_data_json(client_data_b64url: str) -> Optional[Dict[str, Any]]:
    value = (client_data_b64url or "").strip()
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(f"{value}{padding}")
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def get_origin_from_registration_credential(credential: Dict[str, Any]) -> Optional[str]:
    response = credential.get("response") if isinstance(credential, dict) else None
    if not isinstance(response, dict):
        return None
    client_data = response.get("clientDataJSON")
    if not isinstance(client_data, str):
        return None
    parsed = _decode_client_data_json(client_data)
    if not isinstance(parsed, dict):
        return None
    origin = parsed.get("origin")
    if isinstance(origin, str) and origin.strip():
        return origin.strip()
    return None


def get_origin_from_authentication_credential(credential: Dict[str, Any]) -> Optional[str]:
    response = credential.get("response") if isinstance(credential, dict) else None
    if not isinstance(response, dict):
        return None
    client_data = response.get("clientDataJSON")
    if not isinstance(client_data, str):
        return None
    parsed = _decode_client_data_json(client_data)
    if not isinstance(parsed, dict):
        return None
    origin = parsed.get("origin")
    if isinstance(origin, str) and origin.strip():
        return origin.strip()
    return None


def challenge_ttl() -> timedelta:
    settings = get_settings()
    ttl_seconds = max(30, min(settings.passkey_challenge_ttl_seconds, 900))
    return timedelta(seconds=ttl_seconds)


async def create_challenge(
    db: AsyncSession,
    *,
    flow: str,
    user_id: Optional[UUID],
    raw_challenge: str,
    request: Request,
) -> WebAuthnChallenge:
    now = datetime.now(timezone.utc)
    record = WebAuthnChallenge(
        user_id=user_id,
        challenge_hash=challenge_hash(raw_challenge),
        flow=flow,
        expires_at=now + challenge_ttl(),
        request_ip=get_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "").strip()[:512] or None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_valid_challenge(
    db: AsyncSession,
    *,
    flow: str,
    raw_challenge: str,
    user_id: Optional[UUID] = None,
    challenge_id: Optional[UUID] = None,
) -> Optional[WebAuthnChallenge]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(WebAuthnChallenge)
        .where(
            WebAuthnChallenge.flow == flow,
            WebAuthnChallenge.challenge_hash == challenge_hash(raw_challenge),
            WebAuthnChallenge.used_at.is_(None),
            WebAuthnChallenge.expires_at > now,
        )
        .order_by(WebAuthnChallenge.created_at.desc())
    )
    if challenge_id is not None:
        stmt = stmt.where(WebAuthnChallenge.id == challenge_id)
    if user_id is None:
        stmt = stmt.where(WebAuthnChallenge.user_id.is_(None))
    else:
        stmt = stmt.where(WebAuthnChallenge.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def consume_challenge_atomic(
    db: AsyncSession,
    *,
    challenge_id: UUID,
) -> bool:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(WebAuthnChallenge)
        .where(
            WebAuthnChallenge.id == challenge_id,
            WebAuthnChallenge.used_at.is_(None),
            WebAuthnChallenge.expires_at > now,
        )
        .values(used_at=now)
    )
    if (result.rowcount or 0) <= 0:
        await db.rollback()
        return False
    await db.commit()
    return True
