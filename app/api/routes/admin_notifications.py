from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional
from app.db.session import get_db
from app.models import User
from app.models.admin_notification import AdminNotification, AdminNotificationRead, DeviceToken
from app.schemas.admin_notification import (
    AdminNotificationCreate,
    AdminNotificationOut,
    ClientBroadcastNotificationOut,
)

router = APIRouter(tags=["Notifications"])


@router.get("/admin/notifications", response_model=List[AdminNotificationOut])
async def list_admin_notifications(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )

    stmt = (
        select(AdminNotification)
        .order_by(desc(AdminNotification.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/admin/notifications", response_model=AdminNotificationOut)
async def create_admin_notification(
    payload: AdminNotificationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )

    # Calculate total eligible users for this notification
    count_stmt = select(func.count(User.id)).where(User.status == "active")
    if payload.target_audience == "specific" and payload.target_user_email:
        count_stmt = count_stmt.where(User.email == payload.target_user_email.strip().lower())
    
    count_res = await db.execute(count_stmt)
    target_count = count_res.scalar() or 0

    notification = AdminNotification(
        title_fr=payload.title_fr.strip(),
        title_ar=payload.title_ar.strip(),
        message_fr=payload.message_fr.strip(),
        message_ar=payload.message_ar.strip(),
        notification_type=payload.notification_type,
        target_audience=payload.target_audience,
        target_user_email=payload.target_user_email.strip().lower() if payload.target_user_email else None,
        action_type=payload.action_type,
        action_url=payload.action_url.strip() if payload.action_url else None,
        haptic_effect=payload.haptic_effect,
        priority=payload.priority,
        is_active=True,
        sent_count=target_count,
        read_count=0,
        created_by_email=current_user.email,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    # Dispatch real-time FCM push notification directly to devices & topic (Fail-safe)
    device_tokens: list[str] = []
    try:
        token_stmt = select(DeviceToken.token)
        if payload.target_audience == "specific" and payload.target_user_email:
            token_stmt = token_stmt.where(DeviceToken.user_email == payload.target_user_email.strip().lower())
        elif payload.target_audience == "lang_ar":
            token_stmt = token_stmt.where(DeviceToken.language.in_(["ar", "darija"]))
        elif payload.target_audience == "lang_fr":
            token_stmt = token_stmt.where(DeviceToken.language == "fr")
        
        token_res = await db.execute(token_stmt)
        device_tokens = list(token_res.scalars().all())
    except Exception as exc:
        import logging
        logging.getLogger("app.notifications").warning("Could not query device_tokens (table pending or empty): %s", exc)

    try:
        from app.services.fcm_service import send_fcm_broadcast
        await send_fcm_broadcast(
            title_fr=notification.title_fr,
            title_ar=notification.title_ar,
            message_fr=notification.message_fr,
            message_ar=notification.message_ar,
            action_type=notification.action_type,
            action_url=notification.action_url,
            haptic_effect=notification.haptic_effect,
            priority=notification.priority,
            notification_id=notification.id,
            tokens=device_tokens if device_tokens else None,
        )
    except Exception as exc:
        import logging
        logging.getLogger("app.notifications").warning("FCM broadcast dispatch exception: %s", exc)

    return notification


@router.patch("/admin/notifications/{notification_id}/toggle", response_model=AdminNotificationOut)
async def toggle_admin_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )

    result = await db.execute(select(AdminNotification).where(AdminNotification.id == notification_id))
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_active = not notification.is_active
    await db.commit()
    await db.refresh(notification)
    return notification


@router.delete("/admin/notifications/{notification_id}")
async def delete_admin_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )

    result = await db.execute(select(AdminNotification).where(AdminNotification.id == notification_id))
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.delete(notification)
    await db.commit()
    return {"ok": True, "deleted_id": notification_id}


@router.get("/notifications/broadcasts", response_model=List[ClientBroadcastNotificationOut])
async def get_client_broadcast_notifications(
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns active broadcast notifications targeted to the logged-in user (or general audience).
    Translates title/message based on user's preferred language (or default FR).
    """
    # Fetch active notifications
    stmt = (
        select(AdminNotification)
        .where(AdminNotification.is_active == True)
        .order_by(desc(AdminNotification.created_at))
        .limit(30)
    )
    result = await db.execute(stmt)
    notifications = result.scalars().all()

    # Fetch read notifications for this user if logged in
    read_ids: set[int] = set()
    user_lang = "fr"
    user_email = ""
    if current_user is not None:
        read_stmt = select(AdminNotificationRead.notification_id).where(
            AdminNotificationRead.user_id == current_user.id
        )
        read_res = await db.execute(read_stmt)
        read_ids = set(read_res.scalars().all())
        user_lang = (getattr(current_user, "language", None) or "fr").lower()
        user_email = (current_user.email or "").lower()

    output: List[ClientBroadcastNotificationOut] = []
    for n in notifications:
        # Check targeting
        if n.target_audience == "specific":
            if not user_email or not n.target_user_email or n.target_user_email.lower() != user_email:
                continue
        elif n.target_audience == "lang_ar" and user_lang not in {"ar", "darija"}:
            continue
        elif n.target_audience == "lang_fr" and user_lang in {"ar", "darija"}:
            continue

        is_arabic = user_lang in {"ar", "darija"}
        title = n.title_ar if is_arabic and n.title_ar else n.title_fr
        message = n.message_ar if is_arabic and n.message_ar else n.message_fr

        output.append(
            ClientBroadcastNotificationOut(
                id=n.id,
                created_at=n.created_at,
                title=title,
                message=message,
                notification_type=n.notification_type,
                action_type=n.action_type,
                action_url=n.action_url,
                haptic_effect=n.haptic_effect,
                priority=n.priority,
                is_read=n.id in read_ids,
            )
        )

    return output


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Marks a broadcast notification as read for the user.
    """
    # Increment read count on the parent notification
    parent_res = await db.execute(
        select(AdminNotification).where(AdminNotification.id == notification_id)
    )
    parent = parent_res.scalar_one_or_none()
    if parent:
        parent.read_count += 1

    if current_user is not None:
        read_check = await db.execute(
            select(AdminNotificationRead).where(
                AdminNotificationRead.notification_id == notification_id,
                AdminNotificationRead.user_id == current_user.id,
            )
        )
        if not read_check.scalar_one_or_none():
            read_entry = AdminNotificationRead(
                notification_id=notification_id,
                user_id=current_user.id,
            )
            db.add(read_entry)

    await db.commit()
    return {"ok": True, "notification_id": notification_id}


from pydantic import BaseModel


class RegisterDeviceTokenRequest(BaseModel):
    token: str
    platform: str = "android"
    language: str = "fr"


@router.post("/notifications/register-device")
async def register_device_token(
    payload: RegisterDeviceTokenRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Registers or refreshes a device FCM push token in the database.
    """
    token_str = payload.token.strip()
    if not token_str:
        raise HTTPException(status_code=400, detail="Empty device token")

    try:
        from sqlalchemy import text
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS device_tokens (
                id SERIAL PRIMARY KEY,
                token VARCHAR(500) UNIQUE NOT NULL,
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                user_email VARCHAR(255),
                platform VARCHAR(50) DEFAULT 'android' NOT NULL,
                language VARCHAR(10) DEFAULT 'fr' NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_device_tokens_token ON device_tokens(token);
        """))
        await db.commit()

        res = await db.execute(select(DeviceToken).where(DeviceToken.token == token_str))
        existing = res.scalar_one_or_none()

        if existing:
            if current_user:
                existing.user_id = current_user.id
                existing.user_email = current_user.email
            existing.platform = payload.platform
            existing.language = payload.language
        else:
            new_token = DeviceToken(
                token=token_str,
                user_id=current_user.id if current_user else None,
                user_email=current_user.email if current_user else None,
                platform=payload.platform,
                language=payload.language,
            )
            db.add(new_token)

        await db.commit()
    except Exception as exc:
        import logging
        logging.getLogger("app.notifications").warning("Error registering device token: %s", exc)

    return {"ok": True}

