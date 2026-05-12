from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IPBlock


def normalize_ip(ip: str | None) -> str | None:
    if ip is None:
        return None
    value = ip.strip()
    if not value:
        return None
    if value.lower() == "unknown":
        return None
    return value[:64]


async def is_ip_blocked(db: AsyncSession, ip: str | None) -> bool:
    normalized = normalize_ip(ip)
    if not normalized:
        return False
    result = await db.execute(
        select(IPBlock.id).where(IPBlock.ip_address == normalized).limit(1)
    )
    return result.scalar_one_or_none() is not None

