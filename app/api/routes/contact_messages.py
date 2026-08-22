from __future__ import annotations

import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.models.contact_message import ContactMessage
from app.schemas.contact_message import ContactMessageCreate, ContactMessageOut

router = APIRouter()


@router.get("/public/check-account", tags=["public"])
async def check_account_existence(
    contact: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    contact_clean = contact.strip()
    if not contact_clean:
        return {"exists": False}

    stmt = select(User.id).where(
        (func.lower(User.email) == contact_clean.lower()) |
        (User.phone_number == contact_clean)
    )
    result = await db.execute(stmt)
    user_id = result.scalars().first()
    return {"exists": user_id is not None}


@router.post("/public/contact", response_model=ContactMessageOut, tags=["public"])
async def submit_contact_message(
    payload: ContactMessageCreate,
    db: AsyncSession = Depends(get_db),
) -> ContactMessage:
    # Generate random reference number SR-XXXXX
    rand_digits = random.randint(10000, 99999)
    ticket_ref = f"SR-{rand_digits}"

    message = ContactMessage(
        full_name=payload.full_name,
        contact_info=payload.contact_info,
        subject=payload.subject,
        message=payload.message,
        ticket_ref=ticket_ref,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    # Check if a user matches
    info_clean = payload.contact_info.strip()
    user_stmt = select(User.id).where(
        (func.lower(User.email) == info_clean.lower()) |
        (User.phone_number == info_clean)
    )
    user_result = await db.execute(user_stmt)
    matching_user_id = user_result.scalars().first()
    message.matched_user_id = matching_user_id

    return message


@router.get("/admin/contact-messages", response_model=list[ContactMessageOut], tags=["admin"])
async def list_contact_messages(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContactMessage]:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")

    stmt = select(ContactMessage).order_by(desc(ContactMessage.created_at))
    result = await db.execute(stmt)
    messages = list(result.scalars().all())

    if not messages:
        return []

    # Map matched users
    contact_infos = {m.contact_info.strip() for m in messages if m.contact_info}
    lower_infos = [info.lower() for info in contact_infos]

    user_stmt = select(User).where(
        (func.lower(User.email).in_(lower_infos)) |
        (User.phone_number.in_(contact_infos))
    )
    user_result = await db.execute(user_stmt)
    users = list(user_result.scalars().all())

    user_map = {}
    for u in users:
        if u.email:
            user_map[u.email.strip().lower()] = u.id
        if u.phone_number:
            user_map[u.phone_number.strip()] = u.id

    for m in messages:
        info = m.contact_info.strip()
        m.matched_user_id = user_map.get(info.lower()) or user_map.get(info)

    return messages

