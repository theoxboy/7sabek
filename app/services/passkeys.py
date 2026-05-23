from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import get_client_ip
from app.models import WebAuthnChallenge


def challenge_hash(raw_challenge: str) -> str:
    return sha256(raw_challenge.encode("utf-8")).hexdigest()


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
) -> WebAuthnChallenge | None:
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
