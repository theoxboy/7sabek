"""Superadmin view over "Mode Découverte" guest accounts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.admin_activity import create_admin_log
from app.core.guest import protection_level as guest_protection_level
from app.core.rate_limit import get_client_ip
from app.db.session import get_db
from app.models import (
    DeviceAnchor,
    Envelope,
    GuestEvent,
    GuestIdempotencyKey,
    Transaction,
    User,
)
from app.models.transaction import TransactionType
from app.schemas.guest_admin import (
    GuestAdminAnchor,
    GuestAdminDetailOut,
    GuestAdminEnvelope,
    GuestAdminEvent,
    GuestAdminListOut,
    GuestAdminRow,
    GuestAdminTransaction,
)

router = APIRouter(prefix="/admin/guests")

_STALE_DAYS = 30


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="FORBIDDEN")


def _status_of(u: User, tx_count: int, alloc_count: int) -> str:
    if u.claimed_at:
        return "claimed"
    age_days = (
        (datetime.now(timezone.utc) - u.guest_created_at).days
        if u.guest_created_at
        else 0
    )
    if age_days >= _STALE_DAYS and tx_count == 0 and alloc_count == 0:
        return "stale"
    return "active"


async def _claim_method(db: AsyncSession, user_id) -> str | None:
    row = await db.scalar(
        select(GuestEvent.meta)
        .where(GuestEvent.user_id == user_id, GuestEvent.name == "claim_completed")
        .order_by(GuestEvent.created_at.desc())
        .limit(1)
    )
    if isinstance(row, dict):
        method = row.get("method")
        if method in ("passkey", "email", "merge"):
            return method
    return None


@router.get("", response_model=GuestAdminListOut)
async def list_guests(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    protection: Optional[str] = Query(default=None),
    has_tx: Optional[str] = Query(default=None, alias="has_tx"),
    q: Optional[str] = Query(default=None),
    sort: str = Query(default="created_desc"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuestAdminListOut:
    _require_superadmin(current_user)

    try:
        offset = max(0, int(cursor)) if cursor else 0
    except ValueError:
        offset = 0

    env_count = (
        select(Envelope.user_id, func.count().label("n"))
        .group_by(Envelope.user_id)
        .subquery()
    )
    tx_count = (
        select(Transaction.user_id, func.count().label("n"))
        .group_by(Transaction.user_id)
        .subquery()
    )
    tx_expense = (
        select(
            Transaction.user_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .where(Transaction.type == TransactionType.EXPENSE)
        .group_by(Transaction.user_id)
        .subquery()
    )

    stmt = (
        select(
            User,
            func.coalesce(env_count.c.n, 0),
            func.coalesce(tx_count.c.n, 0),
            func.coalesce(tx_expense.c.total, 0),
        )
        .outerjoin(env_count, env_count.c.user_id == User.id)
        .outerjoin(tx_count, tx_count.c.user_id == User.id)
        .outerjoin(tx_expense, tx_expense.c.user_id == User.id)
        .where(User.is_guest.is_(True), User.deleted_at.is_(None))
    )

    if q and q.strip():
        stmt = stmt.where(cast(User.id, String).ilike(f"{q.strip()}%"))

    if protection in ("40", "70"):
        want_ack = protection == "70"
        stmt = stmt.where(
            User.recovery_code_ack_at.is_not(None)
            if want_ack
            else User.recovery_code_ack_at.is_(None)
        )
    if has_tx in ("1", "true", "yes"):
        stmt = stmt.where(func.coalesce(tx_count.c.n, 0) > 0)
    if status_filter == "claimed":
        stmt = stmt.where(User.claimed_at.is_not(None))
    elif status_filter == "stale":
        cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
        stmt = stmt.where(
            User.claimed_at.is_(None),
            User.guest_created_at < cutoff,
            func.coalesce(tx_count.c.n, 0) == 0,
        )
    elif status_filter == "active":
        stmt = stmt.where(User.claimed_at.is_(None))

    total = int(
        await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )

    order = User.guest_created_at.asc() if sort == "created_asc" else User.guest_created_at.desc()
    stmt = stmt.order_by(order).offset(offset).limit(limit)

    rows: list[GuestAdminRow] = []
    for user, n_env, n_tx, expense in (await db.execute(stmt)).all():
        st = _status_of(user, int(n_tx), 0)
        rows.append(
            GuestAdminRow(
                id=str(user.id),
                guest_created_at=user.guest_created_at,
                last_seen_at=user.updated_at,
                protection_level=guest_protection_level(user),
                recovery_code_ack=user.recovery_code_ack_at is not None,
                envelope_count=int(n_env),
                transaction_count=int(n_tx),
                expense_total=f"{float(expense or 0):.2f}",
                status=st,
                claimed_at=user.claimed_at,
                claim_method=(await _claim_method(db, user.id)) if user.claimed_at else None,
                device=None,
                country=user.country,
            )
        )

    if status_filter == "at_risk":
        rows = []  # fragile-context signal not tracked server-side yet

    next_cursor = str(offset + limit) if offset + limit < total else None
    return GuestAdminListOut(rows=rows, total=total, next_cursor=next_cursor)


@router.get("/{guest_id}", response_model=GuestAdminDetailOut)
async def get_guest(
    guest_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuestAdminDetailOut:
    _require_superadmin(current_user)

    user = await db.get(User, guest_id)
    if user is None or not user.is_guest:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    n_env = int(
        await db.scalar(
            select(func.count()).select_from(Envelope).where(Envelope.user_id == user.id)
        )
        or 0
    )
    n_tx = int(
        await db.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.user_id == user.id)
        )
        or 0
    )
    expense = float(
        await db.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.EXPENSE,
            )
        )
        or 0
    )

    events = [
        GuestAdminEvent(name=e.name, at=e.created_at, meta=e.meta)
        for e in (
            await db.execute(
                select(GuestEvent)
                .where(GuestEvent.user_id == user.id)
                .order_by(GuestEvent.created_at.asc())
            )
        ).scalars()
    ]

    envelopes = [
        GuestAdminEnvelope(id=str(env.id), name=env.name)
        for env in (
            await db.execute(select(Envelope).where(Envelope.user_id == user.id).order_by(Envelope.name))
        ).scalars()
    ]

    txs = [
        GuestAdminTransaction(
            id=str(t.id),
            kind=t.type.value if hasattr(t.type, "value") else str(t.type),
            amount=f"{float(t.amount):.2f}",
            label=(t.description or "")[:80],
            occurred_on=t.occurred_on.isoformat(),
        )
        for t in (
            await db.execute(
                select(Transaction)
                .where(Transaction.user_id == user.id)
                .order_by(Transaction.occurred_on.desc(), Transaction.created_at.desc())
                .limit(15)
            )
        ).scalars()
    ]

    anchor_row = (
        await db.execute(
            select(DeviceAnchor).where(DeviceAnchor.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    anchor = None
    device = None
    if anchor_row is not None:
        signals = anchor_row.signals if isinstance(anchor_row.signals, dict) else {}
        device = signals.get("platform")
        anchor = GuestAdminAnchor(
            ip_prefix=None,
            first_seen_at=anchor_row.first_seen,
            last_seen_at=anchor_row.last_seen,
            signal_count=len([v for v in signals.values() if v]),
        )

    return GuestAdminDetailOut(
        id=str(user.id),
        guest_created_at=user.guest_created_at,
        last_seen_at=user.updated_at,
        protection_level=guest_protection_level(user),
        recovery_code_ack=user.recovery_code_ack_at is not None,
        envelope_count=n_env,
        transaction_count=n_tx,
        expense_total=f"{expense:.2f}",
        status=_status_of(user, n_tx, 0),
        claimed_at=user.claimed_at,
        claim_method=(await _claim_method(db, user.id)) if user.claimed_at else None,
        device=device,
        country=user.country,
        events=events,
        envelopes=envelopes,
        recent_transactions=txs,
        anchor=anchor,
    )


@router.delete("/{guest_id}", status_code=status.HTTP_204_NO_CONTENT)
async def purge_guest(
    guest_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    _require_superadmin(current_user)

    from app.api.routes.auth import _purge_guest_owned_rows

    user = await db.get(User, guest_id)
    if user is None or not user.is_guest:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await _purge_guest_owned_rows(db, user.id)
    await db.execute(
        GuestIdempotencyKey.__table__.delete().where(GuestIdempotencyKey.user_id == user.id)
    )
    await db.execute(
        GuestEvent.__table__.delete().where(GuestEvent.user_id == user.id)
    )
    await db.execute(
        DeviceAnchor.__table__.delete().where(DeviceAnchor.user_id == user.id)
    )
    await db.delete(user)
    await db.commit()

    await create_admin_log(
        db,
        event_type="guest_purged",
        status="success",
        message=f"Invité {guest_id} purgé par un superadmin",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
