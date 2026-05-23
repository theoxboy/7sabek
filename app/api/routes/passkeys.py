from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.rate_limit import enforce_rate_limit, get_client_ip
from app.db.session import get_db
from app.models import User, UserPasskey, WebAuthnChallenge
from app.schemas.auth import StatusOut
from app.schemas.passkeys import (
    PasskeyLoginOptionsIn,
    PasskeyLoginOptionsOut,
    PasskeyLoginVerifyIn,
    PasskeyOut,
    PasskeyRegisterOptionsOut,
    PasskeyRegisterVerifyIn,
    PasskeyVerifyPendingOut,
)

router = APIRouter(prefix="/auth/passkeys")
logger = logging.getLogger("app.auth.passkeys")


def _ensure_enabled() -> None:
    settings = get_settings()
    if not settings.enable_passkeys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _challenge_hash(challenge: str) -> str:
    return sha256(challenge.encode("utf-8")).hexdigest()


def _build_challenge_ttl() -> timedelta:
    settings = get_settings()
    ttl_seconds = max(30, min(settings.passkey_challenge_ttl_seconds, 900))
    return timedelta(seconds=ttl_seconds)


def _build_register_public_key(
    *,
    user_id: str,
    user_email: str,
    challenge: str,
    excluded_credential_ids: list[str],
) -> dict:
    settings = get_settings()
    return {
        "challenge": challenge,
        "rp": {
            "id": settings.passkey_rp_id,
            "name": settings.passkey_rp_name,
        },
        "user": {
            "id": user_id,
            "name": user_email,
            "displayName": user_email,
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},
            {"type": "public-key", "alg": -257},
        ],
        "authenticatorSelection": {
            "userVerification": "required",
            "residentKey": "preferred",
        },
        "timeout": int(_build_challenge_ttl().total_seconds() * 1000),
        "attestation": "none",
        "excludeCredentials": [
            {"type": "public-key", "id": value} for value in excluded_credential_ids
        ],
    }


def _build_login_public_key(*, challenge: str, allowed_credential_ids: list[str]) -> dict:
    settings = get_settings()
    return {
        "challenge": challenge,
        "rpId": settings.passkey_rp_id,
        "timeout": int(_build_challenge_ttl().total_seconds() * 1000),
        "userVerification": "required",
        "allowCredentials": [
            {"type": "public-key", "id": value} for value in allowed_credential_ids
        ],
    }


async def _create_challenge(
    db: AsyncSession,
    request: Request,
    *,
    user_id,
    flow: str,
) -> tuple[str, str]:
    challenge = secrets.token_urlsafe(32)
    record = WebAuthnChallenge(
        user_id=user_id,
        challenge_hash=_challenge_hash(challenge),
        flow=flow,
        expires_at=datetime.now(timezone.utc) + _build_challenge_ttl(),
        request_ip=get_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "").strip()[:512] or None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return str(record.id), challenge


async def _consume_challenge(
    db: AsyncSession,
    *,
    challenge_id: UUID,
    challenge: str,
    flow: str,
    user_id,
) -> bool:
    now = datetime.now(timezone.utc)
    stmt = (
        update(WebAuthnChallenge)
        .where(
            WebAuthnChallenge.id == challenge_id,
            WebAuthnChallenge.flow == flow,
            WebAuthnChallenge.challenge_hash == _challenge_hash(challenge),
            WebAuthnChallenge.used_at.is_(None),
            WebAuthnChallenge.expires_at > now,
        )
        .values(used_at=now)
    )
    if user_id is None:
        stmt = stmt.where(WebAuthnChallenge.user_id.is_(None))
    else:
        stmt = stmt.where(WebAuthnChallenge.user_id == user_id)
    result = await db.execute(stmt)
    if (result.rowcount or 0) <= 0:
        await db.rollback()
        return False
    await db.commit()
    return True


@router.post("/register/options", response_model=PasskeyRegisterOptionsOut)
async def passkey_register_options(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasskeyRegisterOptionsOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-register-options", limit=15, window_seconds=60)
    passkey_rows = await db.execute(
        select(UserPasskey.credential_id).where(
            UserPasskey.user_id == user.id,
            UserPasskey.revoked_at.is_(None),
        )
    )
    excluded = [row[0] for row in passkey_rows.all()]
    challenge_id, challenge = await _create_challenge(
        db,
        request,
        user_id=user.id,
        flow="register",
    )
    logger.info("passkey_register_options_created user_id=%s challenge_id=%s", user.id, challenge_id)
    return PasskeyRegisterOptionsOut(
        challenge_id=challenge_id,
        public_key=_build_register_public_key(
            user_id=str(user.id),
            user_email=user.email,
            challenge=challenge,
            excluded_credential_ids=excluded,
        ),
    )


@router.post("/register/verify", response_model=PasskeyVerifyPendingOut, status_code=501)
async def passkey_register_verify(
    payload: PasskeyRegisterVerifyIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasskeyVerifyPendingOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-register-verify", limit=20, window_seconds=60)
    try:
        challenge_uuid = UUID(payload.challenge_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid challenge id") from exc
    consumed = await _consume_challenge(
        db,
        challenge_id=challenge_uuid,
        challenge=payload.challenge,
        flow="register",
        user_id=user.id,
    )
    if not consumed:
        raise HTTPException(status_code=400, detail="Invalid, expired, or used challenge")
    logger.info("passkey_register_verify_pending user_id=%s challenge_id=%s", user.id, payload.challenge_id)
    return PasskeyVerifyPendingOut(
        status="pending",
        message="Passkey verification is not enabled in this phase.",
    )


@router.post("/login/options", response_model=PasskeyLoginOptionsOut)
async def passkey_login_options(
    payload: PasskeyLoginOptionsIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PasskeyLoginOptionsOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-login-options", limit=15, window_seconds=60)
    normalized_email = (payload.email or "").strip().lower()
    target_user_id = None
    allowed_credentials: list[str] = []
    if normalized_email:
        user_result = await db.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
        target_user = user_result.scalar_one_or_none()
        if target_user is not None and target_user.deleted_at is None:
            target_user_id = target_user.id
            passkey_rows = await db.execute(
                select(UserPasskey.credential_id).where(
                    UserPasskey.user_id == target_user.id,
                    UserPasskey.revoked_at.is_(None),
                )
            )
            allowed_credentials = [row[0] for row in passkey_rows.all()]
    challenge_id, challenge = await _create_challenge(
        db,
        request,
        user_id=target_user_id,
        flow="login",
    )
    logger.info(
        "passkey_login_options_created challenge_id=%s has_user=%s",
        challenge_id,
        bool(target_user_id),
    )
    return PasskeyLoginOptionsOut(
        challenge_id=challenge_id,
        public_key=_build_login_public_key(
            challenge=challenge,
            allowed_credential_ids=allowed_credentials,
        ),
    )


@router.post("/login/verify", response_model=PasskeyVerifyPendingOut, status_code=501)
async def passkey_login_verify(
    payload: PasskeyLoginVerifyIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PasskeyVerifyPendingOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-login-verify", limit=20, window_seconds=60)
    try:
        challenge_uuid = UUID(payload.challenge_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid challenge id") from exc
    challenge_id_result = await db.execute(
        select(WebAuthnChallenge.user_id).where(WebAuthnChallenge.id == challenge_uuid)
    )
    challenge_user_id = challenge_id_result.scalar_one_or_none()
    consumed = await _consume_challenge(
        db,
        challenge_id=challenge_uuid,
        challenge=payload.challenge,
        flow="login",
        user_id=challenge_user_id,
    )
    if not consumed:
        raise HTTPException(status_code=400, detail="Invalid, expired, or used challenge")
    logger.info(
        "passkey_login_verify_pending challenge_id=%s user_bound=%s",
        payload.challenge_id,
        bool(challenge_user_id),
    )
    return PasskeyVerifyPendingOut(
        status="pending",
        message="Passkey verification is not enabled in this phase.",
    )


@router.get("", response_model=list[PasskeyOut])
async def list_passkeys(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PasskeyOut]:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-list", limit=30, window_seconds=60)
    rows = await db.execute(
        select(UserPasskey)
        .where(UserPasskey.user_id == user.id)
        .order_by(UserPasskey.created_at.desc())
    )
    records = rows.scalars().all()
    return [
        PasskeyOut(
            id=item.id,
            name=item.name,
            credential_id=item.credential_id,
            aaguid=item.aaguid,
            transports=item.transports,
            created_at=item.created_at,
            last_used_at=item.last_used_at,
            revoked_at=item.revoked_at,
        )
        for item in records
    ]


@router.delete("/{passkey_id}", response_model=StatusOut)
async def delete_passkey(
    passkey_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-delete", limit=20, window_seconds=60)
    result = await db.execute(
        select(UserPasskey).where(
            UserPasskey.id == passkey_id,
            UserPasskey.user_id == user.id,
            UserPasskey.revoked_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Passkey not found")
    record.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("passkey_revoked user_id=%s passkey_id=%s", user.id, passkey_id)
    return StatusOut(status="ok", message="Passkey revoked.")
