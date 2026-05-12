from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rate_limit_bucket import RateLimitBucket


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int
    remaining: int


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


def build_rate_limit_message(retry_after: int) -> str:
    seconds = max(retry_after, 1)
    return f"Trop de tentatives. Réessaie dans {seconds} secondes."


async def check_rate_limit(
    db: AsyncSession,
    key: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    if limit <= 0 or window_seconds <= 0:
        return RateLimitResult(True, retry_after=0, remaining=limit)

    now = datetime.now(timezone.utc)
    bucket = await db.get(RateLimitBucket, key)
    if bucket is None:
        bucket = RateLimitBucket(key=key, window_start=now, count=1)
        db.add(bucket)
        await db.commit()
        return RateLimitResult(True, retry_after=window_seconds, remaining=limit - 1)

    elapsed = (now - bucket.window_start).total_seconds()
    if elapsed >= window_seconds:
        bucket.window_start = now
        bucket.count = 1
        await db.commit()
        return RateLimitResult(True, retry_after=window_seconds, remaining=limit - 1)

    if bucket.count >= limit:
        retry_after = int(window_seconds - elapsed)
        return RateLimitResult(False, retry_after=retry_after, remaining=0)

    bucket.count += 1
    await db.commit()
    remaining = max(limit - bucket.count, 0)
    retry_after = int(window_seconds - elapsed)
    return RateLimitResult(True, retry_after=retry_after, remaining=remaining)


async def enforce_rate_limit(
    db: AsyncSession,
    request: Request,
    key_prefix: str,
    limit: int,
    window_seconds: int,
) -> None:
    if limit <= 0 or window_seconds <= 0:
        return
    ip = get_client_ip(request)
    result = await check_rate_limit(db, f"{key_prefix}:{ip}", limit, window_seconds)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail=build_rate_limit_message(result.retry_after),
            headers={"Retry-After": str(result.retry_after)},
        )
