from __future__ import annotations

import logging
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request, status
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.platform_settings import (
    build_blocked_message,
    build_maintenance_message,
    get_platform_settings,
)
from app.core.user_deletion import build_deleted_account_message
from app.models import User
from app.core.security import decode_token
from app.core.superadmin_session import (
    require_active_account_session,
    require_active_superadmin_session,
)

logger = logging.getLogger("app.deps")

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user_id = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    platform_settings = await get_platform_settings(db)
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=build_deleted_account_message(user, platform_settings),
        )
    blocked_message = build_blocked_message(platform_settings.support_email)

    if user.status == "suspended" and user.suspended_until:
        now = datetime.now(timezone.utc)
        if user.suspended_until <= now:
            user.status = "active"
            user.suspended_until = None
            await db.commit()

    if (
        platform_settings.maintenance_mode
        and user.role != "superadmin"
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=build_maintenance_message(
                platform_settings.maintenance_message
            ),
        )

    if user.role != "superadmin" and user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=blocked_message
        )

    if user.role == "superadmin":
        await require_active_superadmin_session(request, db, user, touch=False)
    else:
        await require_active_account_session(request, db, user, touch=False)

    if (
        user.role == "superadmin"
        and request.headers.get("x-admin-bypass") != "true"
        and not request.url.path.startswith("/auth")
    ):
        target_id = (
            request.headers.get("x-user-id")
            or request.query_params.get("user_id")
        )
        if target_id and target_id != str(user.id):
            try:
                target_uuid = UUID(target_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid user id",
                ) from exc
            target_result = await db.execute(
                select(User).where(User.id == target_uuid)
            )
            target_user = target_result.scalar_one_or_none()
            if target_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
            if target_user.deleted_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
            logger.info(
                "superadmin_impersonation admin_id=%s admin_email=%s "
                "target_id=%s target_email=%s method=%s path=%s",
                user.id,
                user.email,
                target_user.id,
                target_user.email,
                request.method,
                request.url.path,
            )
            return target_user

    return user
