from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
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
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
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
from app.core.config import (
    get_passkey_allowed_origins,
    get_settings,
    is_passkeys_enabled_for_email,
)
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
    challenge_hash,
    get_origin_from_authentication_credential,
    get_origin_from_registration_credential,
    get_valid_challenge_by_id,
    get_valid_challenge,
)

router = APIRouter(prefix="/auth/passkeys")
logger = logging.getLogger("app.auth.passkeys")


def _ensure_enabled() -> None:
    if not get_settings().enable_passkeys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _is_user_allowed_for_passkeys(user: Optional[User]) -> bool:
    return is_passkeys_enabled_for_email(user.email if user is not None else None)


def _ensure_user_allowed(user: User) -> None:
    if not _is_user_allowed_for_passkeys(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _safe_credential_mask(value: str) -> str:
    value = (value or "").strip()
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _safe_email_mask(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if "@" not in raw:
        return "***"
    local, _, domain = raw.partition("@")
    local = local.strip()
    domain = domain.strip()
    if not local or not domain:
        return "***"
    if len(local) <= 2:
        return f"{local[0]}***@{domain}" if len(local) == 2 else f"***@{domain}"
    return f"{local[:2]}***@{domain}"


def _safe_user_label(user: Optional[User]) -> str:
    if user is None:
        return "unknown"
    user_id = str(getattr(user, "id", "") or "")
    user_id_short = user_id[:8] if user_id else "unknown"
    return f"id={user_id_short} email={_safe_email_mask(getattr(user, 'email', None))}"


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


def _build_registration_authenticator_selection() -> tuple[AuthenticatorSelectionCriteria, str, bool]:
    # Safari/iCloud Keychain compatibility: keep UV required and request discoverable creds.
    resident_key_value = "preferred"
    require_resident_key = True
    try:
        selection = AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
            require_resident_key=require_resident_key,
        )
    except TypeError:
        selection = AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            require_resident_key=require_resident_key,
        )
    return selection, resident_key_value, require_resident_key


def _request_origin(request: Request) -> Optional[str]:
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin
    referer = (request.headers.get("referer") or "").strip()
    if not referer:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None
    return None


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
    user_label = _safe_user_label(user)
    logger.info("passkey_register_options_start user=%s", user_label)
    try:
        rows = await db.execute(
            select(UserPasskey.credential_id).where(
                UserPasskey.user_id == user.id,
                UserPasskey.revoked_at.is_(None),
            )
        )
        existing_rows = rows.all()
        logger.info(
            "passkey_register_options_existing_credentials user=%s count=%s",
            user_label,
            len(existing_rows),
        )
        logger.info(
            "passkey_register_options_context user=%s step=%s rp_id=%s rp_name=%s allowed_origins_count=%s selected_user_verification=%s selected_resident_key=%s selected_require_resident_key=%s selected_attestation=%s",
            user_label,
            "before_generate_registration_options",
            get_settings().passkey_rp_id,
            get_settings().passkey_rp_name,
            len(get_passkey_allowed_origins()),
            "required",
            "preferred",
            True,
            "none",
        )
        exclude = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row[0]))
            for row in existing_rows
        ]
        authenticator_selection, resident_key_value, require_resident_key = (
            _build_registration_authenticator_selection()
        )
        attestation_value = "none"
        try:
            options = generate_registration_options(
                rp_id=get_settings().passkey_rp_id,
                rp_name=get_settings().passkey_rp_name,
                user_id=str(user.id).encode("utf-8"),
                user_name=user.email,
                user_display_name=user.email,
                exclude_credentials=exclude,
                authenticator_selection=authenticator_selection,
                attestation=AttestationConveyancePreference.NONE,
            )
        except TypeError:
            options = generate_registration_options(
                rp_id=get_settings().passkey_rp_id,
                rp_name=get_settings().passkey_rp_name,
                user_id=str(user.id).encode("utf-8"),
                user_name=user.email,
                user_display_name=user.email,
                exclude_credentials=exclude,
                authenticator_selection=authenticator_selection,
            )
            attestation_value = "library_default"
        options_json = _parse_options(options_to_json(options))
        challenge = str(options_json.get("challenge") or "").strip()
        if not challenge:
            raise HTTPException(status_code=500, detail="Unable to generate challenge")
        logger.info("passkey_register_options_generated user=%s", user_label)
        logger.info(
            "passkey_register_options_profile user=%s user_verification=%s resident_key=%s require_resident_key=%s attestation=%s",
            user_label,
            "required",
            resident_key_value,
            require_resident_key,
            attestation_value,
        )
        challenge_record = await create_challenge(
            db,
            flow="register",
            user_id=user.id,
            raw_challenge=challenge,
            request=request,
        )
        logger.info(
            "passkey_register_options_challenge_saved user=%s challenge_id=%s",
            user_label,
            challenge_record.id,
        )
        return PasskeyRegisterOptionsOut(
            challenge_id=str(challenge_record.id),
            options=options_json,
        )
    except HTTPException as exc:
        logger.warning(
            "passkey_register_options_error user=%s type=%s message=%s",
            user_label,
            exc.__class__.__name__,
            str(exc.detail),
        )
        raise
    except Exception as exc:
        logger.exception(
            "passkey_register_options_error user=%s type=%s message=%s",
            user_label,
            exc.__class__.__name__,
            str(exc),
        )
        raise


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
    user_label = _safe_user_label(user)
    challenge_id = None
    if payload.challenge_id:
        try:
            challenge_id = UUID(payload.challenge_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid challenge") from exc
    challenge_raw = (payload.challenge or "").strip()
    credential = payload.credential if isinstance(payload.credential, dict) else {}
    credential_response = credential.get("response") if isinstance(credential.get("response"), dict) else {}
    logger.info(
        "register_verify_body_received challenge_id_present=%s credential_keys=%s credential_response_keys=%s",
        bool(challenge_id),
        sorted(credential.keys()),
        sorted(credential_response.keys()),
    )
    if not challenge_raw:
        raise HTTPException(status_code=400, detail="Invalid challenge")
    credential_id_raw = payload.credential.get("id")
    credential_masked = (
        _safe_credential_mask(credential_id_raw)
        if isinstance(credential_id_raw, str) and credential_id_raw.strip()
        else "missing"
    )
    logger.info(
        "passkey_register_verify_start user=%s challenge_id_present=%s credential=%s expected_rp_id=%s allowed_origins=%s request_origin=%s",
        user_label,
        bool(challenge_id),
        credential_masked,
        get_settings().passkey_rp_id,
        get_passkey_allowed_origins(),
        _request_origin(request),
    )
    allowed_origins = get_passkey_allowed_origins()
    credential_origin = get_origin_from_registration_credential(payload.credential)
    logger.info(
        "passkey_register_verify_origin_selection user=%s selected_expected_origin=%s allowed_origins=%s",
        user_label,
        credential_origin,
        allowed_origins,
    )
    if credential_origin not in allowed_origins:
        logger.warning(
            "passkey_register_verify_failed user=%s reason=origin_not_allowed selected_expected_origin=%s allowed_origins=%s",
            user_label,
            credential_origin,
            allowed_origins,
        )
        raise HTTPException(status_code=422, detail="Passkey verification failed")
    challenge = None
    challenge_for_verify = challenge_raw
    if challenge_id is not None:
        challenge = await get_valid_challenge_by_id(
            db,
            flow="register",
            challenge_id=challenge_id,
            user_id=user.id,
        )
        if challenge is not None:
            if challenge_hash(challenge_raw) != challenge.challenge_hash:
                logger.warning(
                    "passkey_register_verify_failed user=%s reason=challenge_mismatch_by_hash challenge_id_present=%s",
                    user_label,
                    bool(challenge_id),
                )
                raise HTTPException(status_code=400, detail="Invalid challenge")
    else:
        challenge = await get_valid_challenge(
            db,
            flow="register",
            raw_challenge=challenge_raw,
            user_id=user.id,
            challenge_id=challenge_id,
        )
    logger.info(
        "passkey_register_verify_challenge_lookup user=%s challenge_found=%s challenge_id_present=%s",
        user_label,
        bool(challenge),
        bool(challenge_id),
    )
    if challenge is None:
        logger.warning(
            "passkey_register_verify_failed user=%s reason=challenge_invalid challenge_id_present=%s credential=%s",
            user_label,
            bool(challenge_id),
            credential_masked,
        )
        raise HTTPException(status_code=400, detail="Invalid challenge")

    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge_for_verify),
            expected_rp_id=get_settings().passkey_rp_id,
            expected_origin=credential_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning(
            "event=passkey_register_verify_failed user=%s exception_type=%s exception_message=%s expected_rp_id=%s credential_origin=%s allowed_origins=%s selected_expected_origin=%s challenge_id_present=%s challenge_found=%s credential_keys=%s credential_response_keys=%s",
            user_label,
            exc.__class__.__name__,
            str(exc),
            get_settings().passkey_rp_id,
            credential_origin,
            allowed_origins,
            credential_origin,
            bool(challenge_id),
            bool(challenge),
            sorted(credential.keys()),
            sorted(credential_response.keys()),
        )
        raise HTTPException(status_code=422, detail="Passkey verification failed")

    credential_id = payload.credential.get("id") or ""
    if not isinstance(credential_id, str) or not credential_id.strip():
        raise HTTPException(status_code=400, detail="Invalid credential payload")
    credential_id = credential_id.strip()

    duplicate_result = await db.execute(
        select(UserPasskey.id).where(UserPasskey.credential_id == credential_id)
    )
    if duplicate_result.scalar_one_or_none() is not None:
        logger.warning(
            "passkey_register_verify_failed user=%s reason=duplicate_credential credential=%s",
            user_label,
            _safe_credential_mask(credential_id),
        )
        raise HTTPException(status_code=409, detail="Credential already registered")

    consumed = await consume_challenge_atomic(db, challenge_id=challenge.id)
    logger.info(
        "passkey_register_verify_challenge_consumed user=%s challenge_consumed=%s",
        user_label,
        bool(consumed),
    )
    if not consumed:
        logger.warning(
            "passkey_register_verify_failed user=%s reason=challenge_reused_or_expired challenge_id_present=%s credential=%s",
            user_label,
            bool(challenge_id),
            _safe_credential_mask(credential_id),
        )
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
        "passkey_register_success user=%s passkey_id=%s credential=%s",
        user_label,
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
    allowed_origins = get_passkey_allowed_origins()
    request_origin = _request_origin(request)
    credential_origin = get_origin_from_authentication_credential(payload.credential)
    credential = payload.credential if isinstance(payload.credential, dict) else {}
    credential_response = credential.get("response") if isinstance(credential.get("response"), dict) else {}
    logger.info(
        "passkey_login_verify_body_received credential_keys=%s credential_response_keys=%s",
        sorted(credential.keys()),
        sorted(credential_response.keys()),
    )
    logger.info(
        "passkey_login_verify_origin_selection selected_expected_origin=%s allowed_origins=%s request_origin=%s expected_rp_id=%s",
        credential_origin,
        allowed_origins,
        request_origin,
        get_settings().passkey_rp_id,
    )
    if credential_origin not in allowed_origins:
        logger.warning(
            "passkey_login_failed reason=origin_not_allowed selected_expected_origin=%s allowed_origins=%s request_origin=%s",
            credential_origin,
            allowed_origins,
            request_origin,
        )
        raise HTTPException(status_code=401, detail="Passkey verification failed")
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
    challenge = None
    challenge_for_verify = payload.challenge
    if challenge_id is not None:
        challenge = await get_valid_challenge_by_id(
            db,
            flow="login",
            challenge_id=challenge_id,
            user_id=passkey.user_id,
        )
        if challenge is None:
            challenge = await get_valid_challenge_by_id(
                db,
                flow="login",
                challenge_id=challenge_id,
                user_id=None,
            )
    else:
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
    logger.info(
        "passkey_login_verify_challenge_lookup challenge_found=%s challenge_id_present=%s",
        bool(challenge),
        bool(challenge_id),
    )

    try:
        verification = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge_for_verify),
            expected_rp_id=get_settings().passkey_rp_id,
            expected_origin=credential_origin,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=int(passkey.sign_count),
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning(
            "passkey_login_failed reason=crypto user_id=%s credential=%s type=%s message=%s expected_rp_id=%s allowed_origins=%s selected_expected_origin=%s request_origin=%s",
            passkey.user_id,
            _safe_credential_mask(passkey.credential_id),
            exc.__class__.__name__,
            str(exc),
            get_settings().passkey_rp_id,
            allowed_origins,
            credential_origin,
            request_origin,
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
    user_label = _safe_user_label(user)
    logger.info("passkeys_list_start user=%s", user_label)
    try:
        rows = await db.execute(
            select(UserPasskey)
            .where(
                UserPasskey.user_id == user.id,
                UserPasskey.revoked_at.is_(None),
            )
            .order_by(UserPasskey.created_at.desc())
        )
        passkeys = rows.scalars().all()
        logger.info(
            "passkeys_list_query_success user=%s count=%s",
            user_label,
            len(passkeys),
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
            for row in passkeys
        ]
    except HTTPException as exc:
        logger.warning(
            "passkeys_list_error user=%s type=%s message=%s",
            user_label,
            exc.__class__.__name__,
            str(exc.detail),
        )
        raise
    except Exception as exc:
        logger.exception(
            "passkeys_list_error user=%s type=%s message=%s",
            user_label,
            exc.__class__.__name__,
            str(exc),
        )
        raise


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
