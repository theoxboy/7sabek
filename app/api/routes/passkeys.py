from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from app.api.deps import get_current_user
from app.api.routes.auth import (
    _build_auth_out,
    _create_or_reuse_account_session,
    _set_auth_cookies,
    _set_superadmin_session_cookie,
    _restore_if_suspension_expired,
)
from app.core.config import get_settings, is_passkeys_enabled_for_email
from app.core.platform_settings import (
    build_blocked_message,
    build_maintenance_message,
    get_platform_settings,
)
from app.core.rate_limit import enforce_rate_limit
from app.core.superadmin_session import validate_superadmin_geo
from app.core.user_deletion import build_deleted_account_message
from app.db.session import get_db
from app.models import User, UserPasskey
from app.schemas.auth import AuthOut
from app.schemas.passkeys import (
    PasskeyDeleteOut,
    PasskeyLoginOptionsIn,
    PasskeyLoginOptionsOut,
    PasskeyLoginVerifyIn,
    PasskeyOut,
    PasskeyRegisterOptionsOut,
    PasskeyRegisterVerifyIn,
    PasskeyRegisterVerifyOut,
    PasskeyStatusOut,
)
from app.services.passkeys import (
    consume_challenge_atomic,
    create_challenge,
    get_valid_challenge,
)

router = APIRouter(prefix="/auth/passkeys")
logger = logging.getLogger("app.auth.passkeys")


def _ensure_enabled() -> None:
    if not get_settings().enable_passkeys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _is_user_allowed_for_passkeys(user: User | None) -> bool:
    return is_passkeys_enabled_for_email(user.email if user is not None else None)


def _ensure_user_allowed(user: User) -> None:
    if not _is_user_allowed_for_passkeys(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _safe_credential_mask(value: str) -> str:
    value = (value or "").strip()
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _parse_options(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


def _extract_credential_id(credential: dict[str, Any]) -> str:
    raw = credential.get("id")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail="Invalid credential payload")
    return raw.strip()


def _bytes_to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@router.get("/status", response_model=PasskeyStatusOut)
async def passkey_status(user: User = Depends(get_current_user)) -> PasskeyStatusOut:
    settings = get_settings()
    if not settings.enable_passkeys:
        return PasskeyStatusOut(enabled=False, reason="disabled")
    if settings.passkeys_allow_all:
        return PasskeyStatusOut(enabled=True, reason="enabled")
    if _is_user_allowed_for_passkeys(user):
        return PasskeyStatusOut(enabled=True, reason="enabled")
    return PasskeyStatusOut(enabled=False, reason="not_allowed")


@router.post("/register/options", response_model=PasskeyRegisterOptionsOut)
async def passkey_register_options(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasskeyRegisterOptionsOut:
    _ensure_enabled()
    _ensure_user_allowed(user)
    await enforce_rate_limit(db, request, "passkeys-register-options", limit=15, window_seconds=60)
    rows = await db.execute(
        select(UserPasskey.credential_id).where(
            UserPasskey.user_id == user.id,
            UserPasskey.revoked_at.is_(None),
        )
    )
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(row[0]))
        for row in rows.all()
    ]
    options = generate_registration_options(
        rp_id=get_settings().passkey_rp_id,
        rp_name=get_settings().passkey_rp_name,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.email,
        user_display_name=user.email,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    options_json = _parse_options(options_to_json(options))
    challenge = str(options_json.get("challenge") or "").strip()
    if not challenge:
        raise HTTPException(status_code=500, detail="Unable to generate challenge")
    challenge_record = await create_challenge(
        db,
        flow="register",
        user_id=user.id,
        raw_challenge=challenge,
        request=request,
    )
    logger.info(
        "passkey_register_options user_id=%s challenge_id=%s",
        user.id,
        challenge_record.id,
    )
    return PasskeyRegisterOptionsOut(
        challenge_id=str(challenge_record.id),
        options=options_json,
    )


@router.post("/register/verify", response_model=PasskeyRegisterVerifyOut)
async def passkey_register_verify(
    payload: PasskeyRegisterVerifyIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasskeyRegisterVerifyOut:
    _ensure_enabled()
    _ensure_user_allowed(user)
    await enforce_rate_limit(db, request, "passkeys-register-verify", limit=20, window_seconds=60)
    challenge_id = None
    if payload.challenge_id:
        try:
            challenge_id = UUID(payload.challenge_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid challenge") from exc
    challenge = await get_valid_challenge(
        db,
        flow="register",
        raw_challenge=payload.challenge,
        user_id=user.id,
        challenge_id=challenge_id,
    )
    if challenge is None:
        raise HTTPException(status_code=400, detail="Invalid challenge")

    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(payload.challenge),
            expected_rp_id=get_settings().passkey_rp_id,
            expected_origin=get_settings().passkey_rp_origin,
            require_user_verification=True,
        )
    except Exception:
        logger.warning("passkey_register_verify_failed user_id=%s", user.id)
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    credential_id = payload.credential.get("id") or ""
    if not isinstance(credential_id, str) or not credential_id.strip():
        raise HTTPException(status_code=400, detail="Invalid credential payload")
    credential_id = credential_id.strip()

    duplicate_result = await db.execute(
        select(UserPasskey.id).where(UserPasskey.credential_id == credential_id)
    )
    if duplicate_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Credential already registered")

    consumed = await consume_challenge_atomic(db, challenge_id=challenge.id)
    if not consumed:
        raise HTTPException(status_code=400, detail="Invalid challenge")

    raw_public_key = bytes(verification.credential_public_key)
    public_key_b64url = _bytes_to_base64url(raw_public_key)
    transports = payload.credential.get("response", {}).get("transports")
    if not isinstance(transports, list):
        transports = None
    passkey = UserPasskey(
        user_id=user.id,
        credential_id=credential_id,
        public_key=public_key_b64url.strip(),
        sign_count=int(verification.sign_count),
        transports=transports,
        aaguid=str(getattr(verification, "aaguid", "") or "") or None,
        name=(payload.name or "").strip()[:120] or None,
    )
    db.add(passkey)
    await db.commit()
    await db.refresh(passkey)
    logger.info(
        "passkey_register_success user_id=%s passkey_id=%s credential=%s",
        user.id,
        passkey.id,
        _safe_credential_mask(passkey.credential_id),
    )
    return PasskeyRegisterVerifyOut(
        status="ok",
        passkey_id=passkey.id,
        name=passkey.name,
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
    target_user = None
    if normalized_email:
        result = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
        candidate = result.scalar_one_or_none()
        if (
            candidate is not None
            and candidate.deleted_at is None
            and _is_user_allowed_for_passkeys(candidate)
        ):
            target_user = candidate

    allow_credentials = []
    if target_user is not None:
        rows = await db.execute(
            select(UserPasskey.credential_id).where(
                UserPasskey.user_id == target_user.id,
                UserPasskey.revoked_at.is_(None),
            )
        )
        allow_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row[0]))
            for row in rows.all()
        ]

    options = generate_authentication_options(
        rp_id=get_settings().passkey_rp_id,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    options_json = _parse_options(options_to_json(options))
    challenge = str(options_json.get("challenge") or "").strip()
    if not challenge:
        raise HTTPException(status_code=500, detail="Unable to generate challenge")
    challenge_record = await create_challenge(
        db,
        flow="login",
        user_id=target_user.id if target_user is not None else None,
        raw_challenge=challenge,
        request=request,
    )
    logger.info(
        "passkey_login_options challenge_id=%s has_user=%s",
        challenge_record.id,
        bool(target_user),
    )
    return PasskeyLoginOptionsOut(
        challenge_id=str(challenge_record.id),
        options=options_json,
    )


@router.post("/login/verify", response_model=AuthOut)
async def passkey_login_verify(
    payload: PasskeyLoginVerifyIn,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    _ensure_enabled()
    await enforce_rate_limit(db, request, "passkeys-login-verify", limit=20, window_seconds=60)
    credential_id = _extract_credential_id(payload.credential)
    passkey_result = await db.execute(
        select(UserPasskey).where(
            UserPasskey.credential_id == credential_id,
            UserPasskey.revoked_at.is_(None),
        )
    )
    passkey = passkey_result.scalar_one_or_none()
    if passkey is None:
        logger.warning("passkey_login_failed reason=credential_not_found")
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    challenge_id = None
    if payload.challenge_id:
        try:
            challenge_id = UUID(payload.challenge_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid challenge") from exc
    challenge = await get_valid_challenge(
        db,
        flow="login",
        raw_challenge=payload.challenge,
        user_id=passkey.user_id,
        challenge_id=challenge_id,
    )
    if challenge is None:
        challenge = await get_valid_challenge(
            db,
            flow="login",
            raw_challenge=payload.challenge,
            user_id=None,
            challenge_id=challenge_id,
        )
    if challenge is None:
        logger.warning("passkey_login_failed reason=challenge_invalid")
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    try:
        verification = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(payload.challenge),
            expected_rp_id=get_settings().passkey_rp_id,
            expected_origin=get_settings().passkey_rp_origin,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=int(passkey.sign_count),
            require_user_verification=True,
        )
    except Exception:
        logger.warning(
            "passkey_login_failed reason=crypto user_id=%s credential=%s",
            passkey.user_id,
            _safe_credential_mask(passkey.credential_id),
        )
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    consumed = await consume_challenge_atomic(db, challenge_id=challenge.id)
    if not consumed:
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    user_result = await db.execute(select(User).where(User.id == passkey.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not _is_user_allowed_for_passkeys(user):
        logger.warning("passkey_login_failed reason=not_allowed")
        raise HTTPException(status_code=401, detail="Passkey verification failed")
    platform_settings = await get_platform_settings(db)
    if user.deleted_at is not None:
        raise HTTPException(status_code=403, detail=build_deleted_account_message(user, platform_settings))
    if platform_settings.maintenance_mode and user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=build_maintenance_message(platform_settings.maintenance_message),
        )
    await _restore_if_suspension_expired(db, user)
    if user.status != "active":
        raise HTTPException(status_code=403, detail=build_blocked_message(platform_settings.support_email))
    if user.must_reset_password:
        raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")
    if user.role == "superadmin":
        validate_superadmin_geo(payload.geo_lat, payload.geo_lng)

    passkey.sign_count = int(verification.new_sign_count)
    passkey.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    session_token = await _create_or_reuse_account_session(
        db,
        request,
        user,
        geo_lat=payload.geo_lat,
        geo_lng=payload.geo_lng,
        geo_accuracy_m=payload.geo_accuracy_m,
        geo_label=payload.geo_label,
        browser=payload.browser,
        os=payload.os,
        device=payload.device,
    )
    _set_auth_cookies(response, str(user.id))
    _set_superadmin_session_cookie(response, session_token)
    logger.info(
        "passkey_login_success user_id=%s credential=%s",
        user.id,
        _safe_credential_mask(passkey.credential_id),
    )
    return _build_auth_out(user)


@router.get("", response_model=list[PasskeyOut])
async def list_passkeys(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PasskeyOut]:
    _ensure_enabled()
    _ensure_user_allowed(user)
    await enforce_rate_limit(db, request, "passkeys-list", limit=30, window_seconds=60)
    rows = await db.execute(
        select(UserPasskey)
        .where(
            UserPasskey.user_id == user.id,
            UserPasskey.revoked_at.is_(None),
        )
        .order_by(UserPasskey.created_at.desc())
    )
    return [
        PasskeyOut(
            id=row.id,
            name=row.name,
            credential_id_masked=_safe_credential_mask(row.credential_id),
            aaguid=row.aaguid,
            transports=row.transports,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )
        for row in rows.scalars().all()
    ]


@router.delete("/{passkey_id}", response_model=PasskeyDeleteOut)
async def delete_passkey(
    passkey_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasskeyDeleteOut:
    _ensure_enabled()
    _ensure_user_allowed(user)
    await enforce_rate_limit(db, request, "passkeys-delete", limit=20, window_seconds=60)
    result = await db.execute(
        select(UserPasskey).where(
            UserPasskey.id == passkey_id,
            UserPasskey.user_id == user.id,
            UserPasskey.revoked_at.is_(None),
        )
    )
    passkey = result.scalar_one_or_none()
    if passkey is None:
        raise HTTPException(status_code=404, detail="Passkey not found")
    passkey.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "passkey_revoked user_id=%s passkey_id=%s credential=%s",
        user.id,
        passkey.id,
        _safe_credential_mask(passkey.credential_id),
    )
    return PasskeyDeleteOut(status="ok", message="Passkey revoked.")
