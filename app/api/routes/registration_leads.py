from __future__ import annotations

from datetime import datetime, timedelta, timezone

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import RegistrationLead, User
from app.schemas.registration_lead import (
    RegistrationLeadDismissIn,
    RegistrationLeadListItemOut,
    RegistrationLeadListOut,
    RegistrationLeadStatsOut,
)

router = APIRouter(prefix="/superadmin/registration-leads")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("", response_model=RegistrationLeadListOut)
async def list_registration_leads(
    q: str = Query(default="", max_length=255),
    status: str = Query(default="", max_length=30),
    has_email: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RegistrationLeadListOut:
    _require_superadmin(current_user)
    query = select(RegistrationLead)
    count_query = select(func.count(RegistrationLead.id))

    filters = []
    if q.strip():
        needle = "%{0}%".format(q.strip().lower())
        filters.append(
            or_(
                func.lower(func.coalesce(RegistrationLead.email, "")).like(needle),
                func.lower(func.coalesce(RegistrationLead.first_name, "")).like(needle),
                func.lower(func.coalesce(RegistrationLead.last_name, "")).like(needle),
                func.lower(func.coalesce(RegistrationLead.phone, "")).like(needle),
            )
        )
    if status.strip():
        filters.append(RegistrationLead.status == status.strip().lower())
    if has_email is not None:
        if has_email:
            filters.append(RegistrationLead.normalized_email.is_not(None))
        else:
            filters.append(RegistrationLead.normalized_email.is_(None))

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    query = query.order_by(RegistrationLead.last_seen_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    rows = list(result.scalars().all())
    total_result = await db.execute(count_query)
    total = int(total_result.scalar_one() or 0)
    return RegistrationLeadListOut(
        items=[RegistrationLeadListItemOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=RegistrationLeadStatsOut)
async def registration_leads_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RegistrationLeadStatsOut:
    _require_superadmin(current_user)
    total = int((await db.execute(select(func.count(RegistrationLead.id)))).scalar_one() or 0)
    email_captured = int((await db.execute(select(func.count(RegistrationLead.id)).where(RegistrationLead.status == "email_captured"))).scalar_one() or 0)
    partial_no_email = int((await db.execute(select(func.count(RegistrationLead.id)).where(RegistrationLead.status == "partial"))).scalar_one() or 0)
    converted = int((await db.execute(select(func.count(RegistrationLead.id)).where(RegistrationLead.status == "converted"))).scalar_one() or 0)
    dismissed = int((await db.execute(select(func.count(RegistrationLead.id)).where(RegistrationLead.status == "dismissed"))).scalar_one() or 0)
    last_24h_from = datetime.now(timezone.utc) - timedelta(hours=24)
    last_24h = int((await db.execute(select(func.count(RegistrationLead.id)).where(RegistrationLead.created_at >= last_24h_from))).scalar_one() or 0)
    return RegistrationLeadStatsOut(
        total=total,
        email_captured=email_captured,
        partial_no_email=partial_no_email,
        converted=converted,
        dismissed=dismissed,
        last_24h=last_24h,
    )


@router.patch("/{lead_id}", response_model=RegistrationLeadListItemOut)
async def update_registration_lead_status(
    lead_id: UUID,
    payload: RegistrationLeadDismissIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RegistrationLeadListItemOut:
    _require_superadmin(current_user)
    next_status = (payload.status or "").strip().lower()
    if next_status not in {"dismissed", "blocked"}:
        raise HTTPException(status_code=422, detail="Invalid status")
    result = await db.execute(select(RegistrationLead).where(RegistrationLead.id == lead_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    item.status = next_status
    item.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return RegistrationLeadListItemOut.model_validate(item)
