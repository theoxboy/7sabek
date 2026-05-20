from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from hashlib import sha256
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.platform_settings import (
    build_blocked_message,
    build_maintenance_message,
    get_platform_settings,
)
from app.core.user_deletion import build_deleted_account_message
from app.core.config import get_settings
from app.core.login_throttle import (
    build_login_throttle_key,
    check_login_throttle,
    clear_login_throttle,
    get_login_throttle_record,
    raise_login_throttle,
    register_login_failure,
)
from app.core.rate_limit import (
    build_rate_limit_message,
    check_rate_limit,
    enforce_rate_limit,
    get_client_ip,
)
from app.core.password_policy import validate_password_easy
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.core.superadmin_session import (
    SUPERADMIN_SESSION_COOKIE,
    generate_superadmin_session_token,
    hash_superadmin_session_token,
    infer_browser,
    infer_device,
    infer_os,
    require_active_account_session,
    require_active_superadmin_session,
    validate_superadmin_geo,
)
from app.db.session import get_db
from app.models import (
    Envelope,
    OnboardingV2Record,
    PasswordResetToken,
    SuperadminSession,
    User,
    WebLoginToken,
)
from app.services.password_reset_mailer import send_password_reset_email
from app.services.onboarding_v2_apply import (
    apply_onboarding_v2_payload,
)
from app.services.onboarding_v2_payload_normalization import (
    derive_canonical_primary_objective,
    normalize_onboarding_answers,
    normalize_onboarding_draft_objects,
)
from app.services.onboarding_v2_record_state import (
    build_onboarding_materialized_state,
    coerce_record_stage_for_write,
)
from app.services.profile_photo import normalize_profile_photo_url
from app.services.sweeps import run_due_sweeps
from app.services.gamification import to_local_date
from app.services.recaptcha import verify_recaptcha_token
from app.schemas.auth import (
    AuthOut,
    ForcePasswordResetIn,
    PasswordResetConfirmIn,
    PasswordResetTokenInfoIn,
    PasswordResetTokenInfoOut,
    PasswordResetRequestIn,
    LoginIn,
    ResolveSuperadminSessionIn,
    ResolveSuperadminSessionOut,
    RegisterIn,
    SuperadminSessionHistoryListOut,
    SuperadminSessionHistoryOut,
    SuperadminSessionOut,
    SuperadminSessionStateOut,
    StatusOut,
    WebLoginTokenOut,
    WebLoginExchangeIn,
)

router = APIRouter(prefix="/auth")
logger = logging.getLogger("app.auth")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _build_register_onboarding_record(payload: RegisterIn, user_id) -> OnboardingV2Record:
    answers = normalize_onboarding_answers(payload.onboarding_v2_answers or {})
    draft_objects = normalize_onboarding_draft_objects(
        payload.onboarding_v2_draft_objects or {},
        answers=answers,
        stored_workflow_stage="in_progress",
    )
    record_payload = {
        "answers": answers,
        "draft_objects": draft_objects,
    }
    stage = coerce_record_stage_for_write(
        "in_progress",
        payload=record_payload,
    )
    record_payload["materialized_state"] = build_onboarding_materialized_state(
        {},
        applied=False,
        workflow_stage=stage,
        payload=record_payload,
    )
    return OnboardingV2Record(
        user_id=user_id,
        flow_version="v2",
        stage=stage,
        income_type=str(answers.get("Q0_income_type") or "").strip() or None,
        primary_objective=derive_canonical_primary_objective(answers),
        household_type=str(answers.get("E0_household_type") or "").strip() or None,
        payload=record_payload,
    )


def _merge_onboarding_payload_materialized_state(
    payload: dict,
    summary: dict,
    *,
    applied: bool = True,
    workflow_stage: str = "completed",
) -> dict:
    next_payload = dict(payload) if isinstance(payload, dict) else {}
    next_payload["materialized_state"] = build_onboarding_materialized_state(
        summary,
        applied=applied,
        workflow_stage=workflow_stage,
        payload=next_payload,
    )
    return next_payload

def _build_auth_out(user: User) -> AuthOut:
    return AuthOut(
        id=str(user.id),
        email=user.email,
        role=user.role,
        status=user.status,
        must_reset_password=user.must_reset_password,
        is_beta_tester=user.is_beta_tester,
        force_onboarding_v2_review=user.force_onboarding_v2_review,
        force_tour_replay_version=user.force_tour_replay_version,
        currency=user.currency,
        sweep_interval_days=user.sweep_interval_days,
        first_name=user.first_name,
        last_name=user.last_name,
        leaderboard_name=user.leaderboard_name,
        phone_number=user.phone_number,
        birth_date=user.birth_date,
        country=user.country,
        city=user.city,
        profile_photo_url=user.profile_photo_url,
    )


async def _restore_if_suspension_expired(db: AsyncSession, user: User) -> None:
    if user.status != "suspended" or not user.suspended_until:
        return
    now = datetime.now(timezone.utc)
    if user.suspended_until <= now:
        user.status = "active"
        user.suspended_until = None
        await db.commit()


def _set_auth_cookies(response: Response, user_id: str) -> None:
    settings = get_settings()
    access_token = create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.access_token_exp_minutes),
    )
    refresh_token = create_token(
        subject=user_id,
        expires_delta=timedelta(days=settings.refresh_token_exp_days),
    )
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_exp_minutes * 60,
        path="/",
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_exp_days * 24 * 60 * 60,
        path="/",
    )


def _set_superadmin_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SUPERADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_exp_days * 24 * 60 * 60,
        path="/",
    )


def _clear_superadmin_session_cookie(response: Response) -> None:
    response.delete_cookie(SUPERADMIN_SESSION_COOKIE, path="/")


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    _clear_superadmin_session_cookie(response)


def _hash_password_reset_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def _build_password_reset_link(raw_token: str) -> str:
    settings = get_settings()
    base = settings.app_base_url.rstrip("/")
    path = settings.password_reset_path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}?token={raw_token}"


def _build_password_reset_block_message(user: User, now: datetime) -> str | None:
    mode = (user.password_reset_block_mode or "none").strip().lower()
    if mode == "permanent":
        return (
            "La réinitialisation du mot de passe est bloquée pour ce compte. "
            "Contacte le support."
        )
    if mode == "temporary":
        until = user.password_reset_blocked_until
        if until and until > now:
            return (
                "La réinitialisation du mot de passe est temporairement bloquée "
                f"jusqu'au {until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."
            )
    return None


def _build_superadmin_session_out(session: SuperadminSession) -> SuperadminSessionOut:
    return SuperadminSessionOut(
        id=session.id,
        source_ip=session.source_ip,
        user_agent=session.user_agent,
        browser=session.browser,
        os=session.os,
        device=session.device,
        geo_lat=session.geo_lat,
        geo_lng=session.geo_lng,
        geo_accuracy_m=session.geo_accuracy_m,
        geo_label=session.geo_label,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
    )


def _build_superadmin_session_history_out(
    session: SuperadminSession,
) -> SuperadminSessionHistoryOut:
    if session.revoked_at is not None:
        status_value = "revoked"
    elif session.ended_at is not None:
        status_value = "ended"
    else:
        status_value = "active"
    return SuperadminSessionHistoryOut(
        id=session.id,
        source_ip=session.source_ip,
        user_agent=session.user_agent,
        browser=session.browser,
        os=session.os,
        device=session.device,
        geo_lat=session.geo_lat,
        geo_lng=session.geo_lng,
        geo_accuracy_m=session.geo_accuracy_m,
        geo_label=session.geo_label,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        ended_at=session.ended_at,
        revoked_at=session.revoked_at,
        status=status_value,
    )


async def _create_or_reuse_account_session(
    db: AsyncSession,
    request: Request,
    user: User,
    *,
    geo_lat: float | None,
    geo_lng: float | None,
    geo_accuracy_m: float | None,
    geo_label: str | None,
    browser: str | None,
    os: str | None,
    device: str | None,
) -> str:
    now = datetime.now(timezone.utc)
    raw_ua = (request.headers.get("user-agent") or "").strip()
    user_agent = raw_ua[:512] if raw_ua else None
    source_ip = get_client_ip(request)

    existing_token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if existing_token:
        existing_hash = hash_superadmin_session_token(existing_token)
        existing_result = await db.execute(
            select(SuperadminSession).where(
                SuperadminSession.user_id == user.id,
                SuperadminSession.session_token_hash == existing_hash,
                SuperadminSession.revoked_at.is_(None),
                SuperadminSession.ended_at.is_(None),
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            existing.last_seen_at = now
            existing.source_ip = source_ip
            existing.user_agent = user_agent
            existing.browser = (browser or "").strip()[:120] or infer_browser(raw_ua)
            existing.os = (os or "").strip()[:120] or infer_os(raw_ua)
            existing.device = (device or "").strip()[:80] or infer_device(raw_ua)
            existing.geo_lat = geo_lat
            existing.geo_lng = geo_lng
            existing.geo_accuracy_m = geo_accuracy_m
            existing.geo_label = (geo_label or "").strip()[:255] or None
            await db.commit()
            return existing_token

    token = generate_superadmin_session_token()
    token_hash = hash_superadmin_session_token(token)
    session = SuperadminSession(
        user_id=user.id,
        session_token_hash=token_hash,
        source_ip=source_ip,
        user_agent=user_agent,
        browser=(browser or "").strip()[:120] or infer_browser(raw_ua),
        os=(os or "").strip()[:120] or infer_os(raw_ua),
        device=(device or "").strip()[:80] or infer_device(raw_ua),
        geo_lat=geo_lat,
        geo_lng=geo_lng,
        geo_accuracy_m=geo_accuracy_m,
        geo_label=(geo_label or "").strip()[:255] or None,
        last_seen_at=now,
    )
    db.add(session)
    await db.commit()
    return token


async def _end_superadmin_session_from_request(
    request: Request,
    db: AsyncSession,
) -> None:
    token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if not token:
        return
    token_hash = hash_superadmin_session_token(token)
    result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.session_token_hash == token_hash,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        return
    session.ended_at = datetime.now(timezone.utc)
    session.last_seen_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterIn,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    normalized_email = _normalize_email(str(payload.email))
    settings = get_settings()
    is_local_env = settings.environment.strip().lower() in {"local", "development", "dev", "test"}
    recaptcha_token = (payload.recaptcha_token or "").strip()
    if settings.recaptcha_enabled:
        if not recaptcha_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_REQUIRED", "message": "أكد أنك ماشي روبوت باش نكملو التسجيل."},
            )
        is_valid_captcha = await verify_recaptcha_token(
            recaptcha_token,
            remote_ip=get_client_ip(request),
            expected_action="register",
            min_score=settings.recaptcha_min_score,
        )
        if not is_valid_captcha:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_FAILED", "message": "ما قدرناش نتحققو من الحماية. عاود المحاولة."},
            )
    elif not is_local_env:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "RECAPTCHA_FAILED", "message": "ما قدرناش نتحققو من الحماية. عاود المحاولة."},
        )
    platform_settings = await get_platform_settings(db)
    await enforce_rate_limit(
        db,
        request,
        "register",
        platform_settings.rate_limit_register_max,
        platform_settings.rate_limit_register_window_minutes * 60,
    )
    if platform_settings.maintenance_mode:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=build_maintenance_message(
                platform_settings.maintenance_message
            ),
        )
    if not platform_settings.registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Les inscriptions sont temporairement fermées.",
        )
    if not payload.mfa_consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA obligatoire: confirme l'activation MFA pour créer le compte.",
        )
    password_error = validate_password_easy(
        payload.password,
        max(platform_settings.password_min_length, 8),
    )
    if password_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=password_error,
        )
    try:
        normalized_profile_photo_url = normalize_profile_photo_url(payload.profile_photo_url)
    except ValueError as exc:
        detail = str(exc)
        if detail == "PROFILE_PHOTO_TOO_LARGE":
            raise HTTPException(status_code=400, detail="Profile photo must be 13 MB or less.") from exc
        raise HTTPException(status_code=400, detail="Invalid profile photo.") from exc
    has_onboarding_payload = bool(
        payload.onboarding_v2_answers and payload.onboarding_v2_draft_objects
    )
    if not has_onboarding_payload and not payload.defer_onboarding_v2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Onboarding v2 required before account creation.",
        )
    existing = await db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    existing_user = existing.scalar_one_or_none()
    if existing_user is not None:
        if existing_user.deleted_at is not None:
            raise HTTPException(
                status_code=403,
                detail=build_deleted_account_message(existing_user, platform_settings),
            )
        if existing_user.password_hash:
            raise HTTPException(status_code=400, detail="Email already exists")

        # Backward-compat: accounts created before password support may have a
        # NULL password_hash. Allow "register" to set the password and claim
        # the account again.
        next_sweep_date = date.today() + timedelta(days=payload.sweep_interval_days)
        existing_user.currency = payload.currency
        existing_user.sweep_interval_days = payload.sweep_interval_days
        existing_user.next_sweep_date = next_sweep_date
        existing_user.password_hash = hash_password(payload.password)
        existing_user.auto_distribution_enabled = (
            platform_settings.default_auto_distribution_enabled
        )
        existing_user.first_name = payload.first_name
        existing_user.last_name = payload.last_name
        existing_user.phone_number = payload.phone_number
        existing_user.birth_date = payload.birth_date
        existing_user.country = payload.country
        existing_user.city = payload.city
        existing_user.profile_photo_url = normalized_profile_photo_url

        envelopes_result = await db.execute(
            select(Envelope).where(Envelope.user_id == existing_user.id)
        )
        envelopes = list(envelopes_result.scalars().all())
        has_savings = any(env.is_default_savings for env in envelopes)
        has_cash = any(getattr(env, "is_cash", False) for env in envelopes)
        if not has_savings:
            db.add(
                Envelope(
                    user_id=existing_user.id,
                    name="Epargnes",
                    is_default_savings=True,
                    deletable=False,
                    rollover_enabled=True,
                )
            )
        if not has_cash:
            db.add(
                Envelope(
                    user_id=existing_user.id,
                    name="Cash",
                    is_cash=True,
                    is_default_savings=False,
                    deletable=False,
                    rollover_enabled=False,
                )
            )
        onboarding_record = None
        if has_onboarding_payload:
            onboarding_record = _build_register_onboarding_record(payload, existing_user.id)
            db.add(onboarding_record)

        await db.flush()
        if has_onboarding_payload and onboarding_record is not None:
            summary = await apply_onboarding_v2_payload(
                db,
                existing_user,
                answers=payload.onboarding_v2_answers or {},
                draft_objects=payload.onboarding_v2_draft_objects or {},
            )
            onboarding_record.payload = _merge_onboarding_payload_materialized_state(
                onboarding_record.payload,
                summary,
                applied=True,
                workflow_stage="completed",
            )
            onboarding_record.stage = "completed"

        await db.commit()
        session_token = await _create_or_reuse_account_session(
            db,
            request,
            existing_user,
            geo_lat=None,
            geo_lng=None,
            geo_accuracy_m=None,
            geo_label=None,
            browser=None,
            os=None,
            device=None,
        )
        _set_auth_cookies(response, str(existing_user.id))
        _set_superadmin_session_cookie(response, session_token)

        return _build_auth_out(existing_user)

    next_sweep_date = date.today() + timedelta(days=payload.sweep_interval_days)
    user = User(
        email=normalized_email,
        currency=payload.currency,
        sweep_interval_days=payload.sweep_interval_days,
        next_sweep_date=next_sweep_date,
        password_hash=hash_password(payload.password),
        auto_distribution_enabled=platform_settings.default_auto_distribution_enabled,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone_number=payload.phone_number,
        birth_date=payload.birth_date,
        country=payload.country,
        city=payload.city,
        profile_photo_url=normalized_profile_photo_url,
    )
    db.add(user)
    await db.flush()

    default_envelope = Envelope(
        user_id=user.id,
        name="Epargnes",
        is_default_savings=True,
        deletable=False,
        rollover_enabled=True,
    )
    db.add(default_envelope)
    cash_envelope = Envelope(
        user_id=user.id,
        name="Cash",
        is_cash=True,
        is_default_savings=False,
        deletable=False,
        rollover_enabled=False,
    )
    db.add(cash_envelope)
    onboarding_record = None
    if has_onboarding_payload:
        onboarding_record = _build_register_onboarding_record(payload, user.id)
        db.add(onboarding_record)
    await db.flush()
    if has_onboarding_payload and onboarding_record is not None:
        summary = await apply_onboarding_v2_payload(
            db,
            user,
            answers=payload.onboarding_v2_answers or {},
            draft_objects=payload.onboarding_v2_draft_objects or {},
        )
        onboarding_record.payload = _merge_onboarding_payload_materialized_state(
            onboarding_record.payload,
            summary,
            applied=True,
            workflow_stage="completed",
        )
        onboarding_record.stage = "completed"
    await db.commit()
    await db.refresh(user)

    session_token = await _create_or_reuse_account_session(
        db,
        request,
        user,
        geo_lat=None,
        geo_lng=None,
        geo_accuracy_m=None,
        geo_label=None,
        browser=None,
        os=None,
        device=None,
    )
    _set_auth_cookies(response, str(user.id))
    _set_superadmin_session_cookie(response, session_token)

    return _build_auth_out(user)


@router.post("/login", response_model=AuthOut)
async def login(
    payload: LoginIn,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    normalized_email = _normalize_email(str(payload.email))
    platform_settings = await get_platform_settings(db)
    await enforce_rate_limit(
        db,
        request,
        "login",
        platform_settings.rate_limit_login_max,
        platform_settings.rate_limit_login_window_minutes * 60,
    )
    login_key = build_login_throttle_key(normalized_email, request)
    locked = await check_login_throttle(db, login_key)
    if locked:
        raise_login_throttle(locked.remaining_seconds)
    result = await db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        throttle = await register_login_failure(db, login_key)
        if throttle:
            raise_login_throttle(throttle.remaining_seconds)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=403,
            detail=build_deleted_account_message(user, platform_settings),
        )
    if not user.password_hash:
        throttle = await register_login_failure(db, login_key)
        if throttle:
            raise_login_throttle(throttle.remaining_seconds)
        raise HTTPException(
            status_code=401,
            detail="Account needs password setup. Use Register to set a password.",
        )
    if not verify_password(payload.password, user.password_hash):
        throttle = await register_login_failure(db, login_key)
        if throttle:
            raise_login_throttle(throttle.remaining_seconds)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    record = await get_login_throttle_record(db, login_key)
    if record and record.force_reset:
        user.must_reset_password = True
        await db.commit()
        await clear_login_throttle(db, login_key)
        raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")
    await clear_login_throttle(db, login_key)
    if platform_settings.maintenance_mode and user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=build_maintenance_message(
                platform_settings.maintenance_message
            ),
        )
    await _restore_if_suspension_expired(db, user)
    if user.status != "active":
        raise HTTPException(
            status_code=403,
            detail=build_blocked_message(platform_settings.support_email),
        )
    if user.must_reset_password:
        raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")

    try:
        await run_due_sweeps(db, user, to_local_date(datetime.now(timezone.utc)))
    except Exception:
        logger.exception("auto_sweep_failed_on_login", extra={"user_id": str(user.id)})

    if user.role == "superadmin":
        validate_superadmin_geo(payload.geo_lat, payload.geo_lng)
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
    return _build_auth_out(user)


@router.post("/refresh", response_model=AuthOut)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    platform_settings = await get_platform_settings(db)
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        user_id = decode_token(refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=403,
            detail=build_deleted_account_message(user, platform_settings),
        )
    if platform_settings.maintenance_mode and user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=build_maintenance_message(
                platform_settings.maintenance_message
            ),
        )
    await _restore_if_suspension_expired(db, user)
    if user.status != "active":
        raise HTTPException(
            status_code=403,
            detail=build_blocked_message(platform_settings.support_email),
        )
    if user.must_reset_password:
        raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")

    session_token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if user.role == "superadmin":
        await require_active_superadmin_session(request, db, user, touch=True)
    else:
        await require_active_account_session(request, db, user, touch=True)

    _set_auth_cookies(response, str(user.id))
    if session_token:
        _set_superadmin_session_cookie(response, session_token)
    return _build_auth_out(user)


@router.post("/force-reset", response_model=AuthOut)
async def force_reset_password(
    payload: ForcePasswordResetIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthOut:
    platform_settings = await get_platform_settings(db)
    normalized_email = _normalize_email(str(payload.email))
    result = await db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=403,
            detail=build_deleted_account_message(user, platform_settings),
        )
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    password_error = validate_password_easy(
        payload.new_password,
        max(platform_settings.password_min_length, 8),
    )
    if password_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=password_error,
        )
    await _restore_if_suspension_expired(db, user)
    if user.status != "active":
        raise HTTPException(
            status_code=403,
            detail=build_blocked_message(platform_settings.support_email),
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_reset_password = False
    await db.commit()

    _set_auth_cookies(response, str(user.id))
    return _build_auth_out(user)


@router.post("/password-reset/request", response_model=StatusOut)
async def request_password_reset(
    payload: PasswordResetRequestIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StatusOut:
    normalized_email = _normalize_email(str(payload.email))
    # Rate limit strategy:
    # - per IP + email to avoid locking all reset attempts for the same IP
    # - stricter than login but less frustrating during normal usage
    ip = get_client_ip(request)
    rate = await check_rate_limit(
        db,
        key=f"password-reset-request:{ip}:{normalized_email}",
        limit=5,
        window_seconds=60 * 60,
    )
    if not rate.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=build_rate_limit_message(rate.retry_after),
            headers={"Retry-After": str(rate.retry_after)},
        )

    result = await db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    user = result.scalar_one_or_none()

    # Do not leak account existence in production responses.
    if user is not None and user.deleted_at is None and user.password_hash:
        now = datetime.now(timezone.utc)
        blocked_message = _build_password_reset_block_message(user, now)
        if blocked_message:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=blocked_message,
            )

        # Security policy: reset links are valid for 5 minutes max.
        token_exp = now + timedelta(minutes=5)

        active_tokens = await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        for active in active_tokens.scalars().all():
            active.used_at = now

        token = ""
        token_hash = ""
        # Security hardening: cryptographically strong token + collision check.
        for _ in range(5):
            candidate = secrets.token_urlsafe(64)
            candidate_hash = _hash_password_reset_token(candidate)
            exists = await db.execute(
                select(PasswordResetToken.id).where(
                    PasswordResetToken.token_hash == candidate_hash
                )
            )
            if exists.scalar_one_or_none() is None:
                token = candidate
                token_hash = candidate_hash
                break
        if not token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to generate reset token.",
            )

        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=token_exp,
                used_at=None,
                request_ip=get_client_ip(request),
            )
        )

        reset_link = _build_password_reset_link(token)
        await db.commit()
        try:
            await send_password_reset_email(
                to_email=user.email,
                reset_link=reset_link,
                locale=(payload.locale or "fr"),
            )
            logger.info("Password reset email delivered for user=%s", user.id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send password reset email for user=%s", user.id)
        return StatusOut(
            status="ok",
            message="Password reset email sent.",
        )

    logger.info("Password reset skipped (no active account) for email=%s", normalized_email)

    return StatusOut(
        status="ok",
        message="If the account exists, a reset email has been sent.",
    )


@router.post("/password-reset/confirm", response_model=StatusOut)
async def confirm_password_reset(
    payload: PasswordResetConfirmIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> StatusOut:
    await enforce_rate_limit(
        db,
        request,
        "password-reset-confirm",
        limit=10,
        window_seconds=15 * 60,
    )

    platform_settings = await get_platform_settings(db)
    password_error = validate_password_easy(
        payload.new_password,
        max(platform_settings.password_min_length, 8),
    )
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    token_hash = _hash_password_reset_token(payload.token.strip())
    now = datetime.now(timezone.utc)
    token_result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
    )
    token_record = token_result.scalar_one_or_none()
    if token_record is None or token_record.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    user_result = await db.execute(select(User).where(User.id == token_record.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        token_record.used_at = now
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    if user.password_hash and verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )

    if user.role == "superadmin":
        settings = get_settings()
        provided_code = (payload.superadmin_code or "").strip()
        provided_first_name = (payload.superadmin_first_name or "").strip().upper()
        expected_code = (settings.superadmin_password_reset_code or "").strip()
        expected_first_name = (
            settings.superadmin_password_reset_first_name or ""
        ).strip().upper()
        if provided_code != expected_code or provided_first_name != expected_first_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Superadmin verification failed.",
            )

    user.password_hash = hash_password(payload.new_password)
    user.must_reset_password = False
    token_record.used_at = now

    user_tokens = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    )
    for extra in user_tokens.scalars().all():
        extra.used_at = now

    sessions = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.user_id == user.id,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
    )
    for session in sessions.scalars().all():
        session.revoked_at = now
        session.last_seen_at = now

    await db.commit()

    _clear_auth_cookies(response)
    return StatusOut(status="ok", message="Password updated. Please sign in.")


@router.post("/password-reset/token-info", response_model=PasswordResetTokenInfoOut)
async def password_reset_token_info(
    payload: PasswordResetTokenInfoIn,
    db: AsyncSession = Depends(get_db),
) -> PasswordResetTokenInfoOut:
    token_hash = _hash_password_reset_token(payload.token.strip())
    now = datetime.now(timezone.utc)
    token_result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
    )
    token_record = token_result.scalar_one_or_none()
    if token_record is None or token_record.expires_at <= now:
        return PasswordResetTokenInfoOut(
            status="ok",
            valid=False,
            requires_superadmin_verification=False,
        )
    user_result = await db.execute(select(User).where(User.id == token_record.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        return PasswordResetTokenInfoOut(
            status="ok",
            valid=False,
            requires_superadmin_verification=False,
        )
    return PasswordResetTokenInfoOut(
        status="ok",
        valid=True,
        requires_superadmin_verification=(user.role == "superadmin"),
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _end_superadmin_session_from_request(request, db)
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=AuthOut)
async def me(
    user: User = Depends(get_current_user),
) -> AuthOut:
    return _build_auth_out(user)


@router.get("/superadmin/sessions", response_model=SuperadminSessionStateOut)
async def superadmin_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuperadminSessionStateOut:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin only")
    current = await require_active_superadmin_session(request, db, user, touch=True)
    sessions_result = await db.execute(
        select(SuperadminSession)
        .where(
            SuperadminSession.user_id == user.id,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
        .order_by(SuperadminSession.created_at.desc())
    )
    sessions = list(sessions_result.scalars().all())
    return SuperadminSessionStateOut(
        current_session_id=current.id,
        has_conflict=len(sessions) > 1,
        sessions=[_build_superadmin_session_out(item) for item in sessions],
    )


@router.get(
    "/superadmin/sessions/history",
    response_model=SuperadminSessionHistoryListOut,
)
async def superadmin_sessions_history(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 500,
) -> SuperadminSessionHistoryListOut:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin only")
    await require_active_superadmin_session(request, db, user, touch=True)
    safe_limit = max(1, min(limit, 2000))
    sessions_result = await db.execute(
        select(SuperadminSession)
        .where(SuperadminSession.user_id == user.id)
        .order_by(SuperadminSession.created_at.desc())
        .limit(safe_limit)
    )
    sessions = list(sessions_result.scalars().all())
    return SuperadminSessionHistoryListOut(
        sessions=[_build_superadmin_session_history_out(item) for item in sessions]
    )


@router.post(
    "/superadmin/sessions/resolve",
    response_model=ResolveSuperadminSessionOut,
)
async def resolve_superadmin_sessions(
    payload: ResolveSuperadminSessionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResolveSuperadminSessionOut:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin only")
    current = await require_active_superadmin_session(request, db, user, touch=False)
    keep_result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.id == payload.keep_session_id,
            SuperadminSession.user_id == user.id,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
    )
    keep = keep_result.scalar_one_or_none()
    if keep is None:
        raise HTTPException(status_code=404, detail="Session cible introuvable.")

    now = datetime.now(timezone.utc)
    sessions_result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.user_id == user.id,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
            SuperadminSession.id != keep.id,
        )
    )
    for item in sessions_result.scalars().all():
        item.revoked_at = now
        item.last_seen_at = now
    keep.last_seen_at = now
    await db.commit()
    return ResolveSuperadminSessionOut(
        status="ok",
        kept_session_id=keep.id,
        should_logout=keep.id != current.id,
    )


@router.post("/web-login-token", response_model=WebLoginTokenOut)
async def web_login_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WebLoginTokenOut:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    record = WebLoginToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    return WebLoginTokenOut(token=token, expires_at=expires_at)


@router.post("/web-login-exchange")
async def web_login_exchange(
    payload: WebLoginExchangeIn,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(WebLoginToken).where(WebLoginToken.token == payload.token)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    now = datetime.now(timezone.utc)
    if record.used_at is not None or record.expires_at < now:
        raise HTTPException(status_code=401, detail="Token expired or used")

    user = await db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    if user.role == "superadmin":
        validate_superadmin_geo(payload.geo_lat, payload.geo_lng)
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

    record.used_at = now
    await db.commit()
    _set_auth_cookies(response, str(user.id))
    _set_superadmin_session_cookie(response, session_token)
    return {"status": "ok"}
