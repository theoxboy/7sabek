from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_activity_log import AdminActivityLog


async def create_admin_log(
    db: AsyncSession,
    event_type: str,
    message: str,
    status: str = "info",
    actor_email: Optional[str] = None,
    actor_ip: Optional[str] = None,
) -> AdminActivityLog:
    record = AdminActivityLog(
        created_at=datetime.now(timezone.utc),
        event_type=event_type,
        status=status,
        message=message,
        actor_email=actor_email,
        actor_ip=actor_ip,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_admin_logs(
    db: AsyncSession,
    limit: int = 50,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    actor_email: Optional[str] = None,
    q: Optional[str] = None,
) -> Sequence[AdminActivityLog]:
    stmt = select(AdminActivityLog)
    if event_type:
        stmt = stmt.where(AdminActivityLog.event_type == event_type)
    if status:
        stmt = stmt.where(AdminActivityLog.status == status)
    if actor_email:
        stmt = stmt.where(
            func.lower(AdminActivityLog.actor_email) == actor_email.strip().lower()
        )
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(AdminActivityLog.message).like(needle)
            | func.lower(AdminActivityLog.event_type).like(needle)
            | func.lower(func.coalesce(AdminActivityLog.actor_email, "")).like(needle)
            | func.lower(func.coalesce(AdminActivityLog.actor_ip, "")).like(needle)
        )
    stmt = stmt.order_by(desc(AdminActivityLog.created_at)).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
