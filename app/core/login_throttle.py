from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import build_rate_limit_message, get_client_ip
from app.models.login_throttle import LoginThrottle


LOCK_RULES = (
    (15, 15 * 60, True),
    (10, 8 * 60, False),
    (5, 60, False),
)


@dataclass
class LoginThrottleState:
    remaining_seconds: int
    force_reset: bool


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def build_login_throttle_key(email: str, request: Request) -> str:
    ip = get_client_ip(request)
    return f"{_normalize_email(email)}:{ip}"


def _lock_for_count(count: int) -> tuple[int, bool]:
    for threshold, seconds, force_reset in LOCK_RULES:
        if count >= threshold:
            return seconds, force_reset
    return 0, False


async def check_login_throttle(
    db: AsyncSession,
    key: str,
) -> LoginThrottleState | None:
    record = await db.get(LoginThrottle, key)
    if record is None or record.locked_until is None:
        return None
    now = datetime.now(timezone.utc)
    if record.locked_until <= now:
        return None
    remaining = int((record.locked_until - now).total_seconds())
    return LoginThrottleState(remaining_seconds=max(remaining, 1), force_reset=record.force_reset)


async def get_login_throttle_record(
    db: AsyncSession,
    key: str,
) -> LoginThrottle | None:
    return await db.get(LoginThrottle, key)


async def register_login_failure(
    db: AsyncSession,
    key: str,
) -> LoginThrottleState | None:
    now = datetime.now(timezone.utc)
    record = await db.get(LoginThrottle, key)
    if record is None:
        record = LoginThrottle(
            key=key,
            failed_count=1,
            updated_at=now,
        )
        db.add(record)
    else:
        record.failed_count += 1
        record.updated_at = now

    lock_seconds, force_reset = _lock_for_count(record.failed_count)
    if lock_seconds:
        record.locked_until = now + timedelta(seconds=lock_seconds)
    if force_reset:
        record.force_reset = True
    await db.commit()

    if lock_seconds:
        return LoginThrottleState(remaining_seconds=lock_seconds, force_reset=record.force_reset)
    return None


async def clear_login_throttle(db: AsyncSession, key: str) -> None:
    record = await db.get(LoginThrottle, key)
    if record is None:
        return
    await db.delete(record)
    await db.commit()


def raise_login_throttle(remaining_seconds: int) -> None:
    raise HTTPException(
        status_code=429,
        detail=build_rate_limit_message(remaining_seconds),
        headers={"Retry-After": str(max(remaining_seconds, 1))},
    )
