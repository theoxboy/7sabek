from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SuperadminSession, User

SUPERADMIN_SESSION_COOKIE = "superadmin_session_token"


def generate_superadmin_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_superadmin_session_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def infer_browser(user_agent: str) -> str:
    ua = user_agent.lower()
    if "edg/" in ua:
        return "Microsoft Edge"
    if "opr/" in ua or "opera" in ua:
        return "Opera"
    if "chrome/" in ua and "chromium" not in ua and "edg/" not in ua:
        return "Google Chrome"
    if "safari/" in ua and "chrome/" not in ua:
        return "Safari"
    if "firefox/" in ua:
        return "Mozilla Firefox"
    return "Unknown"


def infer_os(user_agent: str) -> str:
    ua = user_agent.lower()
    if "windows" in ua:
        return "Windows"
    if "mac os x" in ua or "macintosh" in ua:
        return "macOS"
    if "android" in ua:
        return "Android"
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        return "iOS"
    if "linux" in ua:
        return "Linux"
    return "Unknown"


def infer_device(user_agent: str) -> str:
    ua = user_agent.lower()
    if "ipad" in ua or "tablet" in ua:
        return "Tablet"
    if "mobile" in ua or "iphone" in ua or "android" in ua:
        return "Mobile"
    return "Desktop"


def validate_superadmin_geo(latitude: float | None, longitude: float | None) -> None:
    if latitude is None or longitude is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SUPERADMIN_GEO_REQUIRED",
        )
    if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Coordonnées GPS invalides.",
        )


async def require_active_superadmin_session(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    touch: bool = False,
) -> SuperadminSession:
    return await require_active_account_session(
        request,
        db,
        user,
        touch=touch,
        missing_detail="SUPERADMIN_SESSION_REQUIRED",
        revoked_detail="SUPERADMIN_SESSION_REVOKED",
    )


async def require_active_account_session(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    touch: bool = False,
    missing_detail: str = "SESSION_REQUIRED",
    revoked_detail: str = "SESSION_REVOKED",
) -> SuperadminSession:
    token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=missing_detail,
        )
    token_hash = hash_superadmin_session_token(token)
    result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.user_id == user.id,
            SuperadminSession.session_token_hash == token_hash,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=revoked_detail,
        )
    if touch:
        session.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
    return session
