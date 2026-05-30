from __future__ import annotations

from typing import Optional
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.config import get_settings
from app.models.user import User
from app.schemas.email_center import EmailPreferencePublicOut, EmailPreferenceUpdate, EmailUnsubscribeRequest
from app.services.email_center import (
    get_or_create_email_preferences,
    record_unsubscribe,
    update_email_preferences,
    validate_unsubscribe_token,
)

router = APIRouter(prefix="/email")


def _require_preferences_enabled() -> None:
    if not get_settings().email_center_preferences_enabled:
        raise HTTPException(status_code=403, detail="Email preferences disabled")


@router.get("/unsubscribe")
async def unsubscribe_by_token(
    token: str = Query(..., min_length=10, max_length=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_preferences_enabled()
    parsed = validate_unsubscribe_token(token)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    email = parsed["email"]
    category = parsed["category"]
    user = (await db.execute(select(User).where(User.email == email).limit(1))).scalar_one_or_none()
    if user is not None:
        field_payload = {"{0}_enabled".format(category): False}
        await update_email_preferences(db, user.id, field_payload)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await record_unsubscribe(db, email=email, category=category, token_hash=token_hash)
    return {"status": "ok", "email": email, "category": category}


@router.post("/unsubscribe")
async def unsubscribe_post(payload: EmailUnsubscribeRequest, db: AsyncSession = Depends(get_db)) -> dict:
    return await unsubscribe_by_token(payload.token, db)


@router.get("/preferences", response_model=EmailPreferencePublicOut)
async def get_public_preferences(
    token: str = Query(..., min_length=10, max_length=1000),
    db: AsyncSession = Depends(get_db),
) -> EmailPreferencePublicOut:
    _require_preferences_enabled()
    parsed = validate_unsubscribe_token(token)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    email = parsed["email"]
    user = (await db.execute(select(User).where(User.email == email).limit(1))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    prefs = await get_or_create_email_preferences(db, user.id)
    return EmailPreferencePublicOut(
        email=email,
        salary_reminders_enabled=prefs.salary_reminders_enabled,
        tips_enabled=prefs.tips_enabled,
        product_updates_enabled=prefs.product_updates_enabled,
        marketing_enabled=prefs.marketing_enabled,
        security_emails_enabled=True,
    )


@router.patch("/preferences", response_model=EmailPreferencePublicOut)
async def patch_public_preferences(
    payload: EmailPreferenceUpdate,
    token: str = Query(..., min_length=10, max_length=1000),
    db: AsyncSession = Depends(get_db),
) -> EmailPreferencePublicOut:
    _require_preferences_enabled()
    parsed = validate_unsubscribe_token(token)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    email = parsed["email"]
    user = (await db.execute(select(User).where(User.email == email).limit(1))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    updates = payload.model_dump(exclude_unset=True)
    updates["security_emails_enabled"] = True
    prefs = await update_email_preferences(db, user.id, updates)
    return EmailPreferencePublicOut(
        email=email,
        salary_reminders_enabled=prefs.salary_reminders_enabled,
        tips_enabled=prefs.tips_enabled,
        product_updates_enabled=prefs.product_updates_enabled,
        marketing_enabled=prefs.marketing_enabled,
        security_emails_enabled=True,
    )
