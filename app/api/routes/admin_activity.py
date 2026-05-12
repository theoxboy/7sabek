from __future__ import annotations

from typing import Optional

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.admin_activity import list_admin_logs
from app.core.backup_state import is_backup_in_progress, list_backup_logs
from app.db.session import get_db
from app.models import User
from app.schemas.admin_activity import AdminActivityLogOut

router = APIRouter(prefix="/admin/activity")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _matches_filters(
    log: AdminActivityLogOut,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    actor_email: Optional[str] = None,
    q: Optional[str] = None,
) -> bool:
    if event_type and log.event_type != event_type:
        return False
    if status and log.status != status:
        return False
    if actor_email:
        if not log.actor_email:
            return False
        if log.actor_email.strip().lower() != actor_email.strip().lower():
            return False
    if q:
        needle = q.strip().lower()
        if needle:
            haystack = " ".join(
                [
                    log.message,
                    log.event_type,
                    log.actor_email or "",
                    log.actor_ip or "",
                ]
            ).lower()
            if needle not in haystack:
                return False
    return True


@router.get("", response_model=list[AdminActivityLogOut])
async def get_admin_activity(
    limit: int = Query(default=50, ge=1, le=200),
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    actor_email: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdminActivityLogOut]:
    _require_superadmin(current_user)
    memory_logs = list_backup_logs(limit=limit)

    memory_logs_out = [
        AdminActivityLogOut(
            id=log.id,
            created_at=log.created_at,
            actor_email=log.actor_email,
            actor_ip=log.actor_ip,
            event_type=log.event_type,
            status=log.status,
            message=log.message,
        )
        for log in memory_logs
    ]
    if event_type or status or actor_email or q:
        memory_logs_out = [
            log
            for log in memory_logs_out
            if _matches_filters(log, event_type, status, actor_email, q)
        ]

    if is_backup_in_progress():
        return memory_logs_out[:limit]
    try:
        db_logs = await list_admin_logs(
            db,
            limit=limit,
            event_type=event_type,
            status=status,
            actor_email=actor_email,
            q=q,
        )
    except Exception:
        return memory_logs_out[:limit]
    combined = [
        AdminActivityLogOut.model_validate(item)
        for item in db_logs
    ] + memory_logs_out
    combined_sorted = sorted(combined, key=lambda log: log.created_at, reverse=True)
    return combined_sorted[:limit]


@router.get("/export")
async def export_admin_activity(
    limit: int = Query(default=1000, ge=1, le=5000),
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    actor_email: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    _require_superadmin(current_user)

    memory_logs = list_backup_logs(limit=limit)
    memory_logs_out = [
        AdminActivityLogOut(
            id=log.id,
            created_at=log.created_at,
            actor_email=log.actor_email,
            actor_ip=log.actor_ip,
            event_type=log.event_type,
            status=log.status,
            message=log.message,
        )
        for log in memory_logs
    ]
    if event_type or status or actor_email or q:
        memory_logs_out = [
            log
            for log in memory_logs_out
            if _matches_filters(log, event_type, status, actor_email, q)
        ]

    if is_backup_in_progress():
        combined_sorted = sorted(
            memory_logs_out, key=lambda log: log.created_at, reverse=True
        )[:limit]
    else:
        try:
            db_logs = await list_admin_logs(
                db,
                limit=limit,
                event_type=event_type,
                status=status,
                actor_email=actor_email,
                q=q,
            )
        except Exception:
            db_logs = []
        combined = [
            AdminActivityLogOut.model_validate(item)
            for item in db_logs
        ] + memory_logs_out
        combined_sorted = sorted(combined, key=lambda log: log.created_at, reverse=True)[
            :limit
        ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "created_at",
            "event_type",
            "status",
            "message",
            "actor_email",
            "actor_ip",
        ]
    )
    for log in combined_sorted:
        writer.writerow(
            [
                log.created_at.isoformat(),
                log.event_type,
                log.status,
                log.message,
                log.actor_email or "",
                log.actor_ip or "",
            ]
        )

    response = StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
    )
    response.headers["Content-Disposition"] = (
        'attachment; filename="audit_logs.csv"'
    )
    return response
