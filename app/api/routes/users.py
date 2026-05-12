from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import get_current_user
from app.core.admin_activity import create_admin_log
from app.core.platform_settings import get_platform_settings
from app.core.password_policy import validate_password_easy
from app.core.ip_block import normalize_ip
from app.core.rate_limit import get_client_ip
from app.core.security import hash_password
from app.core.superadmin_session import (
    SUPERADMIN_SESSION_COOKIE,
    hash_superadmin_session_token,
)
from app.core.user_deletion import (
    build_deleted_account_message,
    deletion_grace_deadline,
    is_within_deletion_grace,
)
from app.services.leaderboard_name import (
    apply_leaderboard_name_or_suspend,
    is_leaderboard_name_banned,
    validate_leaderboard_name,
)
from app.services.onboarding_v2_apply import (
    apply_onboarding_v2_payload,
)
from app.services.onboarding_v2_validation import (
    build_onboarding_validation_error_detail,
    validate_onboarding_answers,
)
from app.services.onboarding_v2_payload_normalization import (
    derive_canonical_primary_objective,
    normalize_onboarding_answers,
    normalize_onboarding_draft_objects,
)
from app.services.onboarding_v2_record_state import (
    build_onboarding_materialized_state,
    coerce_record_stage_for_write,
    has_valid_applied_materialized_state,
    normalize_record_payload_for_response,
    normalize_workflow_stage,
)
from app.services.gamification import to_local_date, month_start
from app.services.profile_photo import normalize_profile_photo_url
from app.db.session import get_db
from app.models import (
    Category,
    CategoryEnvelopeMap,
    DistributionLog,
    DistributionRule,
    DistributionItem,
    DistributionRun,
    DistributionRunItem,
    Envelope,
    EnvelopeAllocation,
    EnvelopeAdjustmentLog,
    EnvelopeMovement,
    EnvelopePeriod,
    EnvelopeTransferLog,
    Goal,
    IPBlock,
    OnboardingV2Record,
    PasswordResetToken,
    PageView,
    Sweep,
    SuperadminSession,
    Transaction,
    TransactionType,
    User,
    UserShiftPilotState,
    LeaderboardNameChange,
)
from app.schemas.user import (
    UserCreate,
    UserDataSummaryOut,
    UserOut,
    AdminSummaryOut,
    TopClientOut,
    AdminUserUpdate,
    AdminPasswordReset,
    PasswordResetBlockOut,
    PasswordResetBlockUpdateIn,
    UserSessionActionIn,
    UserSessionActionOut,
    BlockedIPListOut,
    BlockedIPOut,
    UnblockIPOut,
    UserSessionBlockIPIn,
    UserSessionBlockIPOut,
    UserSessionHistoryListOut,
    UserSessionHistoryOut,
)
from app.schemas.onboarding_v2 import (
    OnboardingV2ApplyOut,
    OnboardingV2AdminRecordListOut,
    OnboardingV2AdminRecordOut,
    OnboardingV2RecordCreateIn,
    OnboardingV2RecordOut,
)
from app.schemas.user_profile import UserProfileUpdate
from app.schemas.user_settings import UserSettingsOut, UserSettingsUpdate
from app.schemas.shiftpilot import ShiftPilotStateOut, ShiftPilotStateUpsertIn

router = APIRouter(prefix="/users")


def _build_user_session_history_out(
    session: SuperadminSession,
    *,
    blocked_ips: Optional[set[str]] = None,
) -> UserSessionHistoryOut:
    if session.revoked_at is not None:
        status_value = "revoked"
    elif session.ended_at is not None:
        status_value = "ended"
    else:
        status_value = "active"
    return UserSessionHistoryOut(
        id=session.id,
        status=status_value,
        source_ip=session.source_ip,
        user_agent=session.user_agent,
        browser=session.browser,
        os=session.os,
        device=session.device,
        geo_lat=session.geo_lat,
        geo_lng=session.geo_lng,
        geo_accuracy_m=session.geo_accuracy_m,
        geo_label=session.geo_label,
        ip_blocked=bool(
            blocked_ips and normalize_ip(session.source_ip) in blocked_ips
        ),
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        ended_at=session.ended_at,
        revoked_at=session.revoked_at,
    )


def _build_blocked_ip_out(
    block: IPBlock,
    *,
    blocked_by_email: Optional[str] = None,
    source_user_id: Optional[UUID] = None,
    source_user_email: Optional[str] = None,
) -> BlockedIPOut:
    return BlockedIPOut(
        id=block.id,
        ip_address=block.ip_address,
        reason=block.reason,
        created_at=block.created_at,
        blocked_by_user_id=block.blocked_by_user_id,
        blocked_by_email=blocked_by_email,
        source_session_id=block.source_session_id,
        source_user_id=source_user_id,
        source_user_email=source_user_email,
    )


def _safe_string(value: Any) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _normalize_shiftpilot_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail="Invalid shiftpilot payload") from exc
    if len(serialized) > 2_000_000:
        raise HTTPException(
            status_code=413,
            detail="ShiftPilot payload too large (max 2MB)",
        )
    return value


def _build_onboarding_record_out(
    record: OnboardingV2Record,
    *,
    user_email: Optional[str] = None,
    user_first_name: Optional[str] = None,
    user_last_name: Optional[str] = None,
) -> OnboardingV2AdminRecordOut:
    normalized_payload, effective_stage = normalize_record_payload_for_response(
        record.payload if isinstance(record.payload, dict) else {},
        stored_workflow_stage=record.stage,
    )
    normalized_answers = (
        normalized_payload.get("answers")
        if isinstance(normalized_payload.get("answers"), dict)
        else {}
    )
    return OnboardingV2AdminRecordOut(
        id=record.id,
        user_id=record.user_id,
        flow_version=record.flow_version,
        stage=effective_stage,
        income_type=record.income_type,
        primary_objective=derive_canonical_primary_objective(normalized_answers)
        or record.primary_objective,
        household_type=record.household_type,
        payload=normalized_payload,
        created_at=record.created_at,
        updated_at=record.updated_at,
        user_email=user_email,
        user_first_name=user_first_name,
        user_last_name=user_last_name,
    )


def _merge_onboarding_payload_materialized_state(
    payload: dict[str, Any],
    summary: dict[str, Any],
    *,
    applied: bool = True,
    workflow_stage: str = "completed",
) -> dict[str, Any]:
    next_payload = dict(payload) if isinstance(payload, dict) else {}
    next_payload["materialized_state"] = build_onboarding_materialized_state(
        summary,
        applied=applied,
        workflow_stage=workflow_stage,
        payload=next_payload,
    )
    return next_payload


def _has_applied_onboarding_materialized_state(payload: Any) -> bool:
    return has_valid_applied_materialized_state(payload if isinstance(payload, dict) else {})


async def _build_user_out_many(
    db: AsyncSession, users: list[User]
) -> list[UserOut]:
    if not users:
        return []

    user_ids = [user.id for user in users]
    records_result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id.in_(user_ids))
        .order_by(OnboardingV2Record.user_id, OnboardingV2Record.updated_at.desc())
    )
    onboarding_done_by_user_id: dict[UUID, bool] = {}
    for record in records_result.scalars().all():
        if record.user_id in onboarding_done_by_user_id:
            continue
        onboarding_done_by_user_id[record.user_id] = (
            _has_applied_onboarding_materialized_state(record.payload)
        )

    reset_stats_result = await db.execute(
        select(
            PasswordResetToken.user_id,
            func.count(PasswordResetToken.id),
            func.max(PasswordResetToken.created_at),
        )
        .where(PasswordResetToken.user_id.in_(user_ids))
        .group_by(PasswordResetToken.user_id)
    )
    reset_stats: dict[UUID, tuple[int, Optional[datetime]]] = {
        row[0]: (int(row[1] or 0), row[2]) for row in reset_stats_result.all()
    }

    serialized: list[UserOut] = []
    now = datetime.now(timezone.utc)
    for user in users:
        payload = UserOut.model_validate(user).model_dump()
        payload["has_completed_onboarding_v2"] = onboarding_done_by_user_id.get(
            user.id, False
        )
        total_requests, last_requested_at = reset_stats.get(user.id, (0, None))
        payload["password_reset_requests_total"] = total_requests
        payload["password_reset_last_requested_at"] = last_requested_at
        raw_mode = (user.password_reset_block_mode or "none").strip().lower()
        raw_until = user.password_reset_blocked_until
        if raw_mode == "permanent":
            effective_mode = "permanent"
            blocked = True
            blocked_until = None
        elif raw_mode == "temporary" and raw_until and raw_until > now:
            effective_mode = "temporary"
            blocked = True
            blocked_until = raw_until
        else:
            effective_mode = "none"
            blocked = False
            blocked_until = None
        payload["password_reset_blocked"] = blocked
        payload["password_reset_block_mode"] = effective_mode
        payload["password_reset_blocked_until"] = blocked_until
        payload["password_reset_block_reason"] = (
            user.password_reset_block_reason if blocked else None
        )
        payload["password_reset_blocked_at"] = (
            user.password_reset_blocked_at if blocked else None
        )
        serialized.append(UserOut.model_validate(payload))
    return serialized


async def _build_user_out(db: AsyncSession, user: User) -> UserOut:
    return (await _build_user_out_many(db, [user]))[0]


@router.get("/admin/summary", response_model=AdminSummaryOut)
async def get_admin_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminSummaryOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user_count = await db.scalar(select(func.count()).select_from(User))
    category_count = await db.scalar(select(func.count()).select_from(Category))
    envelope_count = await db.scalar(select(func.count()).select_from(Envelope))
    transaction_count = await db.scalar(select(func.count()).select_from(Transaction))
    return AdminSummaryOut(
        users=int(user_count or 0),
        categories=int(category_count or 0),
        envelopes=int(envelope_count or 0),
        transactions=int(transaction_count or 0),
    )


@router.get("/admin/top-clients", response_model=list[TopClientOut])
async def get_admin_top_clients(
    limit: int = 4,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TopClientOut]:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    limit = max(1, min(limit, 20))
    result = await db.execute(
        select(
            User.id,
            User.email,
            User.first_name,
            User.last_name,
            func.coalesce(func.sum(Transaction.amount), 0).label("income_total"),
        )
        .join(Transaction, Transaction.user_id == User.id)
        .where(Transaction.type == TransactionType.INCOME)
        .group_by(User.id, User.email, User.first_name, User.last_name)
        .order_by(desc("income_total"))
        .limit(limit)
    )
    rows = result.all()
    if not rows:
        fallback = await db.execute(
            select(
                User.id,
                User.email,
                User.first_name,
                User.last_name,
                func.coalesce(func.sum(Transaction.amount), 0).label("income_total"),
            )
            .join(Transaction, Transaction.user_id == User.id)
            .group_by(User.id, User.email, User.first_name, User.last_name)
            .order_by(desc("income_total"))
            .limit(limit)
        )
        rows = fallback.all()
    return [
        TopClientOut(
            user_id=row.id,
            email=row.email,
            first_name=row.first_name,
            last_name=row.last_name,
            income_total=float(row.income_total or 0),
        )
        for row in rows
    ]


@router.get("/admin/ip-blocks", response_model=BlockedIPListOut)
async def list_blocked_ips(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BlockedIPListOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    safe_limit = max(1, min(limit, 2000))
    blocker_user = aliased(User)
    source_user = aliased(User)
    result = await db.execute(
        select(
            IPBlock,
            blocker_user.email.label("blocked_by_email"),
            SuperadminSession.user_id.label("source_user_id"),
            source_user.email.label("source_user_email"),
        )
        .outerjoin(blocker_user, blocker_user.id == IPBlock.blocked_by_user_id)
        .outerjoin(SuperadminSession, SuperadminSession.id == IPBlock.source_session_id)
        .outerjoin(source_user, source_user.id == SuperadminSession.user_id)
        .order_by(IPBlock.created_at.desc())
        .limit(safe_limit)
    )
    items: list[BlockedIPOut] = []
    for row in result.all():
        items.append(
            _build_blocked_ip_out(
                row[0],
                blocked_by_email=row[1],
                source_user_id=row[2],
                source_user_email=row[3],
            )
        )
    return BlockedIPListOut(items=items)


@router.delete("/admin/ip-blocks/{block_id}", response_model=UnblockIPOut)
async def unblock_ip(
    block_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UnblockIPOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(IPBlock).where(IPBlock.id == block_id))
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="IP block not found")
    ip_address = block.ip_address
    await db.delete(block)
    await db.commit()
    await create_admin_log(
        db,
        event_type="ip_unblocked",
        status="success",
        message=f"IP débloquée {ip_address}",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )
    return UnblockIPOut(status="ok", id=block_id, ip_address=ip_address)


@router.get("", response_model=list[UserOut])
async def list_users(
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserOut]:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    query = select(User).order_by(User.created_at.desc())
    if not include_deleted:
        query = query.where(User.deleted_at.is_(None))
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(User.email).like(needle)
            | func.lower(func.coalesce(User.first_name, "")).like(needle)
            | func.lower(func.coalesce(User.last_name, "")).like(needle)
        )
    query = query.offset(offset).limit(min(limit, 200))
    result = await db.execute(query)
    users = list(result.scalars().all())
    return await _build_user_out_many(db, users)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if current_user.deleted_at is None:
        current_user.deleted_at = datetime.now(timezone.utc)
        await db.commit()


@router.post(
    "/me/onboarding-v2-records",
    response_model=OnboardingV2RecordOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_onboarding_v2_record(
    payload: OnboardingV2RecordCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingV2RecordOut:
    answers = normalize_onboarding_answers(payload.answers)
    validation_errors = validate_onboarding_answers(answers)
    if validation_errors and (payload.stage or "in_progress") in {"review", "completed"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_onboarding_validation_error_detail(validation_errors),
        )
    draft_objects = normalize_onboarding_draft_objects(
        payload.draft_objects,
        answers=answers,
        stored_workflow_stage=payload.stage or "in_progress",
    )
    record_payload = {
        "answers": answers,
        "draft_objects": draft_objects,
    }
    stage = coerce_record_stage_for_write(
        payload.stage or "in_progress",
        payload=record_payload,
    )
    record_payload = _merge_onboarding_payload_materialized_state(
        record_payload,
        {},
        applied=False,
        workflow_stage=stage,
    )
    record = OnboardingV2Record(
        user_id=current_user.id,
        flow_version=(payload.flow_version or "v2").strip()[:32] or "v2",
        stage=stage,
        income_type=_safe_string(answers.get("Q0_income_type")),
        primary_objective=derive_canonical_primary_objective(answers),
        household_type=_safe_string(answers.get("E0_household_type")),
        payload=record_payload,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _build_onboarding_record_out(record)


@router.put("/me/onboarding-v2-records/latest", response_model=OnboardingV2RecordOut)
async def upsert_my_latest_onboarding_v2_record(
    payload: OnboardingV2RecordCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingV2RecordOut:
    answers = normalize_onboarding_answers(payload.answers)
    validation_errors = validate_onboarding_answers(answers)
    if validation_errors and (payload.stage or "in_progress") in {"review", "completed"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_onboarding_validation_error_detail(validation_errors),
        )
    draft_objects = normalize_onboarding_draft_objects(
        payload.draft_objects,
        answers=answers,
        stored_workflow_stage=payload.stage or "in_progress",
    )
    record_payload = {
        "answers": answers,
        "draft_objects": draft_objects,
    }
    stage = coerce_record_stage_for_write(
        payload.stage or "in_progress",
        payload=record_payload,
    )
    record_payload = _merge_onboarding_payload_materialized_state(
        record_payload,
        {},
        applied=False,
        workflow_stage=stage,
    )
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == current_user.id)
        .order_by(desc(OnboardingV2Record.created_at))
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = OnboardingV2Record(
            user_id=current_user.id,
            flow_version=(payload.flow_version or "v2").strip()[:32] or "v2",
            stage=stage,
            income_type=_safe_string(answers.get("Q0_income_type")),
            primary_objective=derive_canonical_primary_objective(answers),
            household_type=_safe_string(answers.get("E0_household_type")),
            payload=record_payload,
        )
        db.add(record)
    else:
        record.flow_version = (
            (payload.flow_version or record.flow_version or "v2").strip()[:32] or "v2"
        )
        record.stage = stage
        record.income_type = _safe_string(answers.get("Q0_income_type"))
        record.primary_objective = derive_canonical_primary_objective(answers)
        record.household_type = _safe_string(answers.get("E0_household_type"))
        record.payload = record_payload

    await db.commit()
    await db.refresh(record)
    return _build_onboarding_record_out(record)


@router.post(
    "/me/onboarding-v2-records/latest/apply",
    response_model=OnboardingV2ApplyOut,
)
async def apply_my_latest_onboarding_v2_record(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingV2ApplyOut:
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == current_user.id)
        .order_by(desc(OnboardingV2Record.created_at))
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Onboarding record not found")

    payload, effective_record_stage = normalize_record_payload_for_response(
        record.payload if isinstance(record.payload, dict) else {},
        stored_workflow_stage=record.stage,
    )
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    validation_errors = validate_onboarding_answers(answers)
    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_onboarding_validation_error_detail(validation_errors),
        )
    draft_objects = (
        payload.get("draft_objects") if isinstance(payload.get("draft_objects"), dict) else {}
    )
    try:
        summary = await apply_onboarding_v2_payload(
            db,
            current_user,
            answers=answers,
            draft_objects=draft_objects,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") != "ONBOARDING_APPLY_PRECONDITIONS_FAILED":
            raise
        failed_summary = {
            "workflow_stage": normalize_workflow_stage(effective_record_stage, default="review"),
            "validation_stage": "invalid",
            "materialization_stage": "not_applied",
            "blocking_errors": detail.get("blocking_errors", []),
            "validation_warnings": detail.get("warnings", []),
            "distribution_setup_valid": detail.get("is_valid", False),
            "distribution_status": detail.get("distribution_status"),
            "distribution_source": detail.get("distribution_source"),
            "distribution_setup_source": detail.get("distribution_source"),
            "distribution_eligible_total": detail.get("eligible_total", 0),
            "distribution_covered_total": detail.get("covered_total", 0),
            "distribution_unresolved_total": detail.get("unresolved_total", 0),
            "distribution_unresolved_envelope_names": detail.get(
                "unresolved_envelope_names", []
            ),
            "distribution_missing_envelope_names": detail.get(
                "missing_envelope_names", []
            ),
            "distribution_active_config_id": detail.get("active_config_id"),
        }
        record.payload = _merge_onboarding_payload_materialized_state(
            payload,
            failed_summary,
            applied=False,
            workflow_stage=(
                effective_record_stage
                if effective_record_stage in {"in_progress", "review"}
                else "review"
            ),
        )
        record.primary_objective = derive_canonical_primary_objective(answers)
        record.stage = (
            effective_record_stage
            if effective_record_stage in {"in_progress", "review"}
            else "review"
        )
        await db.commit()
        raise

    record.payload = _merge_onboarding_payload_materialized_state(
        payload,
        summary,
        applied=True,
        workflow_stage="completed",
    )
    record.primary_objective = derive_canonical_primary_objective(answers)
    record.stage = "completed" if summary.get("validation_stage") == "valid" else "review"
    current_user.force_onboarding_v2_review = False
    await db.commit()
    await db.refresh(record)
    materialized_state = (
        record.payload.get("materialized_state")
        if isinstance(record.payload, dict)
        else None
    )
    response_summary = (
        materialized_state.get("summary")
        if isinstance(materialized_state, dict)
        and isinstance(materialized_state.get("summary"), dict)
        else summary
    )
    return OnboardingV2ApplyOut(record_id=record.id, applied=True, **response_summary)


@router.get("/me/onboarding-v2-records", response_model=list[OnboardingV2RecordOut])
async def list_my_onboarding_v2_records(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OnboardingV2RecordOut]:
    safe_limit = max(1, min(limit, 100))
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == current_user.id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(safe_limit)
    )
    return [
        _build_onboarding_record_out(item)
        for item in result.scalars().all()
    ]


@router.get("/me/shiftpilot-state", response_model=ShiftPilotStateOut)
async def get_my_shiftpilot_state(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ShiftPilotStateOut:
    result = await db.execute(
        select(UserShiftPilotState).where(
            UserShiftPilotState.user_id == current_user.id
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = UserShiftPilotState(user_id=current_user.id, payload={})
        db.add(state)
        await db.commit()
        await db.refresh(state)
    return ShiftPilotStateOut.model_validate(state)


@router.put("/me/shiftpilot-state", response_model=ShiftPilotStateOut)
async def upsert_my_shiftpilot_state(
    payload: ShiftPilotStateUpsertIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ShiftPilotStateOut:
    normalized_payload = _normalize_shiftpilot_payload(payload.payload)
    result = await db.execute(
        select(UserShiftPilotState).where(
            UserShiftPilotState.user_id == current_user.id
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = UserShiftPilotState(
            user_id=current_user.id,
            payload=normalized_payload,
        )
        db.add(state)
    else:
        state.payload = normalized_payload

    await db.commit()
    await db.refresh(state)
    return ShiftPilotStateOut.model_validate(state)


@router.get(
    "/admin/onboarding-v2-records",
    response_model=OnboardingV2AdminRecordListOut,
)
async def list_admin_onboarding_v2_records(
    limit: int = 200,
    offset: int = 0,
    user_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingV2AdminRecordListOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(offset, 0)
    query = (
        select(OnboardingV2Record, User.email, User.first_name, User.last_name)
        .join(User, User.id == OnboardingV2Record.user_id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    if user_id is not None:
        query = query.where(OnboardingV2Record.user_id == user_id)
    result = await db.execute(query)
    items = [
        _build_onboarding_record_out(
            row[0],
            user_email=row[1],
            user_first_name=row[2],
            user_last_name=row[3],
        )
        for row in result.all()
    ]
    return OnboardingV2AdminRecordListOut(items=items)


@router.get(
    "/{user_id}/onboarding-v2-records",
    response_model=OnboardingV2AdminRecordListOut,
)
async def list_user_onboarding_v2_records(
    user_id: UUID,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OnboardingV2AdminRecordListOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    safe_limit = max(1, min(limit, 1000))
    result = await db.execute(
        select(OnboardingV2Record, User.email, User.first_name, User.last_name)
        .join(User, User.id == OnboardingV2Record.user_id)
        .where(OnboardingV2Record.user_id == user_id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(safe_limit)
    )
    items = [
        _build_onboarding_record_out(
            row[0],
            user_email=row[1],
            user_first_name=row[2],
            user_last_name=row[3],
        )
        for row in result.all()
    ]
    return OnboardingV2AdminRecordListOut(items=items)


@router.get("/{user_id}/sessions", response_model=UserSessionHistoryListOut)
async def list_user_sessions(
    user_id: UUID,
    limit: int = 300,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSessionHistoryListOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    safe_limit = max(1, min(limit, 2000))
    sessions_result = await db.execute(
        select(SuperadminSession)
        .where(SuperadminSession.user_id == user.id)
        .order_by(SuperadminSession.created_at.desc())
        .limit(safe_limit)
    )
    sessions = list(sessions_result.scalars().all())
    ip_values = {
        normalized
        for normalized in (normalize_ip(item.source_ip) for item in sessions)
        if normalized
    }
    blocked_ips: set[str] = set()
    if ip_values:
        blocked_result = await db.execute(
            select(IPBlock.ip_address).where(IPBlock.ip_address.in_(ip_values))
        )
        blocked_ips = {str(item) for item in blocked_result.scalars().all()}
    return UserSessionHistoryListOut(
        user_id=user.id,
        user_email=user.email,
        sessions=[
            _build_user_session_history_out(item, blocked_ips=blocked_ips)
            for item in sessions
        ],
    )


@router.post(
    "/{user_id}/sessions/{session_id}/action",
    response_model=UserSessionActionOut,
)
async def act_on_user_session(
    user_id: UUID,
    session_id: UUID,
    payload: UserSessionActionIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSessionActionOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    session_result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.id == session_id,
            SuperadminSession.user_id == user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    now = datetime.now(timezone.utc)
    if payload.action == "end":
        if session.revoked_at is None and session.ended_at is None:
            session.ended_at = now
            session.last_seen_at = now
    else:
        if session.revoked_at is None:
            session.revoked_at = now
            session.last_seen_at = now

    await db.commit()
    await db.refresh(session)

    should_logout = False
    actor_token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if actor_token:
        actor_hash = hash_superadmin_session_token(actor_token)
        should_logout = actor_hash == session.session_token_hash
    blocked_ips: set[str] = set()
    normalized_ip = normalize_ip(session.source_ip)
    if normalized_ip:
        blocked_result = await db.execute(
            select(IPBlock.ip_address).where(IPBlock.ip_address == normalized_ip)
        )
        if blocked_result.scalar_one_or_none():
            blocked_ips.add(normalized_ip)

    return UserSessionActionOut(
        status="ok",
        action=payload.action,
        user_id=user.id,
        session=_build_user_session_history_out(session, blocked_ips=blocked_ips),
        should_logout=should_logout,
    )


@router.post(
    "/{user_id}/sessions/{session_id}/block-ip",
    response_model=UserSessionBlockIPOut,
)
async def block_user_session_ip(
    user_id: UUID,
    session_id: UUID,
    payload: UserSessionBlockIPIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSessionBlockIPOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    session_result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.id == session_id,
            SuperadminSession.user_id == user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    target_ip = normalize_ip(session.source_ip)
    if not target_ip:
        raise HTTPException(
            status_code=400,
            detail="Cette session ne contient pas d'adresse IP exploitable.",
        )
    actor_ip = normalize_ip(get_client_ip(request))
    if actor_ip == target_ip:
        raise HTTPException(
            status_code=400,
            detail="Impossible de bloquer votre IP courante depuis cette session.",
        )

    existing_block = await db.execute(
        select(IPBlock).where(IPBlock.ip_address == target_ip)
    )
    block = existing_block.scalar_one_or_none()
    already_blocked = block is not None
    if block is None:
        block = IPBlock(
            ip_address=target_ip,
            reason=(payload.reason or "").strip()[:255] or None,
            blocked_by_user_id=current_user.id,
            source_session_id=session.id,
        )
        db.add(block)

    now = datetime.now(timezone.utc)
    same_ip_active_result = await db.execute(
        select(SuperadminSession).where(
            SuperadminSession.source_ip == target_ip,
            SuperadminSession.revoked_at.is_(None),
            SuperadminSession.ended_at.is_(None),
        )
    )
    affected = 0
    affected_hashes: set[str] = set()
    for item in same_ip_active_result.scalars().all():
        item.revoked_at = now
        item.last_seen_at = now
        affected += 1
        affected_hashes.add(item.session_token_hash)
    if session.revoked_at is None:
        session.revoked_at = now
        session.last_seen_at = now

    await db.commit()
    await db.refresh(session)

    should_logout = False
    actor_token = request.cookies.get(SUPERADMIN_SESSION_COOKIE)
    if actor_token:
        actor_hash = hash_superadmin_session_token(actor_token)
        should_logout = actor_hash in affected_hashes

    await create_admin_log(
        db,
        event_type="ip_blocked",
        status="success",
        message=(
            f"IP bloquée {target_ip} pour {user.email} depuis session {session.id} "
            f"(déjà bloquée={already_blocked}, sessions impactées={affected})"
        ),
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )

    return UserSessionBlockIPOut(
        status="ok",
        blocked_ip=target_ip,
        already_blocked=already_blocked,
        affected_active_sessions=affected,
        user_id=user.id,
        session=_build_user_session_history_out(session, blocked_ips={target_ip}),
        should_logout=should_logout,
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user_by_id(
    user_id: UUID,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.deleted_at is not None and not include_deleted:
        raise HTTPException(status_code=400, detail="User is deleted")
    return await _build_user_out(db, user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user_by_id(
    user_id: UUID,
    payload: AdminUserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        normalized_profile_photo_url = normalize_profile_photo_url(payload.profile_photo_url)
    except ValueError as exc:
        detail = str(exc)
        if detail == "PROFILE_PHOTO_TOO_LARGE":
            raise HTTPException(status_code=400, detail="Profile photo must be 13 MB or less.") from exc
        raise HTTPException(status_code=400, detail="Invalid profile photo.") from exc
    update_data = payload.model_dump(exclude_unset=True)
    previous_email = user.email

    if payload.email and payload.email != user.email:
        existing = await db.execute(select(User).where(User.email == payload.email))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="Email already exists")
        user.email = payload.email

    if payload.currency is not None:
        user.currency = payload.currency
    if payload.sweep_interval_days is not None:
        user.sweep_interval_days = payload.sweep_interval_days
        user.next_sweep_date = date.today() + timedelta(days=payload.sweep_interval_days)
    if payload.first_name is not None:
        user.first_name = payload.first_name
    if payload.last_name is not None:
        user.last_name = payload.last_name
    if payload.leaderboard_name is not None:
        trimmed = payload.leaderboard_name.strip()
        if trimmed:
            cleaned = validate_leaderboard_name(trimmed)
            if is_leaderboard_name_banned(cleaned):
                raise HTTPException(status_code=400, detail="PSEUDO_BLOCKED_FOR_ABUSE")
            user.leaderboard_name = cleaned
        else:
            user.leaderboard_name = None
    if payload.phone_number is not None:
        user.phone_number = payload.phone_number
    if payload.birth_date is not None:
        user.birth_date = payload.birth_date
    if payload.country is not None:
        user.country = payload.country
    if payload.city is not None:
        user.city = payload.city
    if payload.profile_photo_url is not None:
        user.profile_photo_url = normalized_profile_photo_url
    if payload.status is not None:
        status_value = payload.status.lower().strip()
        if status_value not in {"active", "limited", "suspended"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        user.status = status_value
    if payload.must_reset_password is not None:
        user.must_reset_password = payload.must_reset_password
    if payload.is_beta_tester is not None:
        user.is_beta_tester = payload.is_beta_tester
    if payload.force_onboarding_v2_review is not None:
        user.force_onboarding_v2_review = payload.force_onboarding_v2_review
    if payload.force_tour_replay_version is not None:
        user.force_tour_replay_version = payload.force_tour_replay_version

    await db.commit()
    await db.refresh(user)
    if update_data:
        updated_fields = ", ".join(sorted(update_data.keys()))
        email_label = (
            f"{previous_email} → {user.email}"
            if previous_email and previous_email != user.email
            else user.email
        )
        await create_admin_log(
            db,
            event_type="user_updated",
            status="success",
            message=f"Utilisateur mis à jour: {email_label} ({updated_fields})",
            actor_email=current_user.email,
            actor_ip=get_client_ip(request),
        )
    return await _build_user_out(db, user)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: UUID,
    payload: AdminPasswordReset,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    platform_settings = await get_platform_settings(db)
    password_error = validate_password_easy(
        payload.password,
        max(platform_settings.password_min_length, 8),
    )
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.password)
    await db.commit()
    await create_admin_log(
        db,
        event_type="user_password_reset",
        status="success",
        message=f"Mot de passe réinitialisé pour {user.email}",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )


@router.post("/{user_id}/password-reset-block", response_model=PasswordResetBlockOut)
async def set_password_reset_block(
    user_id: UUID,
    payload: PasswordResetBlockUpdateIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PasswordResetBlockOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    mode = payload.mode.strip().lower()
    reason = (payload.reason or "").strip()[:255] or None
    now = datetime.now(timezone.utc)

    blocked = False
    blocked_until: Optional[datetime] = None
    if mode == "none":
        user.password_reset_block_mode = "none"
        user.password_reset_blocked_until = None
        user.password_reset_block_reason = None
        user.password_reset_blocked_at = None
        user.password_reset_blocked_by_user_id = None
    elif mode == "permanent":
        user.password_reset_block_mode = "permanent"
        user.password_reset_blocked_until = None
        user.password_reset_block_reason = reason
        user.password_reset_blocked_at = now
        user.password_reset_blocked_by_user_id = current_user.id
        blocked = True
    else:
        if payload.duration_value is None or payload.duration_unit is None:
            raise HTTPException(
                status_code=400,
                detail="duration_value and duration_unit are required for temporary block",
            )
        if payload.duration_unit == "hours":
            delta = timedelta(hours=payload.duration_value)
        elif payload.duration_unit == "days":
            delta = timedelta(days=payload.duration_value)
        else:
            delta = timedelta(days=payload.duration_value * 30)
        blocked_until = now + delta
        user.password_reset_block_mode = "temporary"
        user.password_reset_blocked_until = blocked_until
        user.password_reset_block_reason = reason
        user.password_reset_blocked_at = now
        user.password_reset_blocked_by_user_id = current_user.id
        blocked = True

    await db.commit()
    await db.refresh(user)

    if mode == "none":
        log_message = f"Blocage reset-password retiré pour {user.email}"
    elif mode == "permanent":
        log_message = f"Blocage reset-password permanent pour {user.email}"
    else:
        log_message = (
            f"Blocage reset-password temporaire pour {user.email} jusqu'au "
            f"{blocked_until.isoformat() if blocked_until else 'N/A'}"
        )
    await create_admin_log(
        db,
        event_type="user_password_reset_block_updated",
        status="success",
        message=log_message,
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )

    return PasswordResetBlockOut(
        status="ok",
        user_id=user.id,
        blocked=blocked,
        mode=user.password_reset_block_mode or "none",
        blocked_until=user.password_reset_blocked_until,
        reason=user.password_reset_block_reason,
        blocked_at=user.password_reset_blocked_at,
    )


async def _delete_user_by_id(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        delete(UserShiftPilotState).where(UserShiftPilotState.user_id == user_id)
    )
    await db.execute(delete(DistributionLog).where(DistributionLog.user_id == user_id))
    await db.execute(delete(DistributionRule).where(DistributionRule.user_id == user_id))
    await db.execute(
        delete(DistributionRunItem).where(
            DistributionRunItem.run_id.in_(
                select(DistributionRun.id).where(DistributionRun.user_id == user_id)
            )
        )
    )
    await db.execute(delete(DistributionRun).where(DistributionRun.user_id == user_id))
    await db.execute(delete(DistributionItem).where(DistributionItem.user_id == user_id))
    await db.execute(delete(Sweep).where(Sweep.user_id == user_id))
    await db.execute(delete(EnvelopeMovement).where(EnvelopeMovement.user_id == user_id))
    await db.execute(delete(EnvelopeAllocation).where(EnvelopeAllocation.user_id == user_id))
    await db.execute(delete(EnvelopePeriod).where(EnvelopePeriod.user_id == user_id))
    await db.execute(
        delete(EnvelopeTransferLog).where(EnvelopeTransferLog.user_id == user_id)
    )
    await db.execute(
        delete(EnvelopeAdjustmentLog).where(EnvelopeAdjustmentLog.user_id == user_id)
    )
    await db.execute(delete(Goal).where(Goal.user_id == user_id))
    await db.execute(
        delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user_id)
    )
    await db.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await db.execute(delete(Category).where(Category.user_id == user_id))
    await db.execute(delete(Envelope).where(Envelope.user_id == user_id))
    await db.execute(delete(PageView).where(PageView.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_by_id(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    email = user.email
    if user.deleted_at is None:
        user.deleted_at = datetime.now(timezone.utc)
        await db.commit()
    await create_admin_log(
        db,
        event_type="user_deleted",
        status="success",
        message=f"Utilisateur supprimé (grâce): {email}",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )


@router.post("/{user_id}/restore", response_model=UserOut)
async def restore_user_by_id(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.deleted_at is None:
        raise HTTPException(status_code=400, detail="User is not deleted")
    settings = await get_platform_settings(db)
    if not is_within_deletion_grace(user, settings):
        deadline = deletion_grace_deadline(user, settings)
        message = (
            "Délai de restauration expiré."
            if not deadline
            else f"Délai expiré depuis {deadline.strftime('%Y-%m-%d %H:%M UTC')}."
        )
        raise HTTPException(status_code=400, detail=message)
    user.deleted_at = None
    await db.commit()
    await db.refresh(user)
    await create_admin_log(
        db,
        event_type="user_restored",
        status="success",
        message=f"Utilisateur restauré: {user.email}",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )
    return await _build_user_out(db, user)


@router.post("/{user_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_user_by_id(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    email = user.email
    await _delete_user_by_id(db, user_id)
    await db.commit()
    await create_admin_log(
        db,
        event_type="user_purged",
        status="warning",
        message=f"Utilisateur supprimé définitivement: {email}",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
    )

@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, db: AsyncSession = Depends(get_db)
) -> UserOut:
    platform_settings = await get_platform_settings(db)
    existing = await db.execute(select(User).where(User.email == payload.email))
    existing_user = existing.scalar_one_or_none()
    if existing_user is not None:
        if existing_user.deleted_at is not None:
            raise HTTPException(
                status_code=400,
                detail=build_deleted_account_message(existing_user, platform_settings),
            )
        raise HTTPException(status_code=400, detail="Email already exists")
    try:
        normalized_profile_photo_url = normalize_profile_photo_url(payload.profile_photo_url)
    except ValueError as exc:
        detail = str(exc)
        if detail == "PROFILE_PHOTO_TOO_LARGE":
            raise HTTPException(status_code=400, detail="Profile photo must be 13 MB or less.") from exc
        raise HTTPException(status_code=400, detail="Invalid profile photo.") from exc

    next_sweep_date = date.today() + timedelta(days=payload.sweep_interval_days)
    user = User(
        email=payload.email,
        currency=payload.currency,
        sweep_interval_days=payload.sweep_interval_days,
        next_sweep_date=next_sweep_date,
        auto_distribution_enabled=platform_settings.default_auto_distribution_enabled,
        first_name=payload.first_name,
        last_name=payload.last_name,
        leaderboard_name=payload.leaderboard_name,
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
    await db.commit()
    await db.refresh(user)

    return await _build_user_out(db, user)


@router.get("/me/settings", response_model=UserSettingsOut)
async def get_user_settings(
    current_user: User = Depends(get_current_user),
) -> UserSettingsOut:
    return UserSettingsOut(
        currency=current_user.currency,
        sweep_interval_days=current_user.sweep_interval_days,
        auto_distribution_enabled=current_user.auto_distribution_enabled,
        auto_sweep_enabled=current_user.auto_sweep_enabled,
        next_sweep_date=current_user.next_sweep_date,
    )


@router.get("/me/summary", response_model=UserDataSummaryOut)
async def get_user_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserDataSummaryOut:
    user_id = current_user.id
    tx_count = await db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id)
    )
    category_count = await db.scalar(
        select(func.count()).select_from(Category).where(Category.user_id == user_id)
    )
    envelope_count = await db.scalar(
        select(func.count())
        .select_from(Envelope)
        .where(Envelope.user_id == user_id, Envelope.deletable.is_(True))
    )
    return UserDataSummaryOut(
        transactions=int(tx_count or 0),
        categories=int(category_count or 0),
        envelopes=int(envelope_count or 0),
    )


@router.patch("/me/settings", response_model=UserSettingsOut)
async def update_user_settings(
    payload: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSettingsOut:
    if payload.sweep_interval_days is not None:
        current_user.sweep_interval_days = payload.sweep_interval_days
        if payload.next_sweep_date is None:
            current_user.next_sweep_date = date.today() + timedelta(
                days=payload.sweep_interval_days
            )
    if payload.currency is not None:
        current_user.currency = payload.currency
    if payload.auto_distribution_enabled is not None:
        current_user.auto_distribution_enabled = payload.auto_distribution_enabled
    if payload.auto_sweep_enabled is not None:
        current_user.auto_sweep_enabled = payload.auto_sweep_enabled
    if payload.next_sweep_date is not None:
        current_user.next_sweep_date = payload.next_sweep_date
    await db.commit()
    await db.refresh(current_user)
    return UserSettingsOut(
        currency=current_user.currency,
        sweep_interval_days=current_user.sweep_interval_days,
        auto_distribution_enabled=current_user.auto_distribution_enabled,
        auto_sweep_enabled=current_user.auto_sweep_enabled,
        next_sweep_date=current_user.next_sweep_date,
    )


@router.patch("/me/profile", response_model=UserOut)
async def update_user_profile(
    payload: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    try:
        normalized_profile_photo_url = normalize_profile_photo_url(payload.profile_photo_url)
    except ValueError as exc:
        detail = str(exc)
        if detail == "PROFILE_PHOTO_TOO_LARGE":
            raise HTTPException(status_code=400, detail="Profile photo must be 13 MB or less.") from exc
        raise HTTPException(status_code=400, detail="Invalid profile photo.") from exc
    if payload.first_name is not None:
        current_user.first_name = payload.first_name.strip() or None
    if payload.last_name is not None:
        current_user.last_name = payload.last_name.strip() or None
    if payload.leaderboard_name is not None:
        trimmed = payload.leaderboard_name.strip()
        if not trimmed:
            raise HTTPException(status_code=400, detail="PSEUDO_REQUIRED")
        cleaned, blocked = apply_leaderboard_name_or_suspend(current_user, trimmed)
        if blocked:
            current_user.leaderboard_name = None
            await db.commit()
            raise HTTPException(status_code=403, detail="PSEUDO_BLOCKED_FOR_ABUSE")
        if cleaned != (current_user.leaderboard_name or ""):
            today = to_local_date(datetime.now(timezone.utc))
            start = month_start(today)
            if start.month == 12:
                end = date(start.year + 1, 1, 1)
            else:
                end = date(start.year, start.month + 1, 1)
            changes_result = await db.execute(
                select(func.count(LeaderboardNameChange.id)).where(
                    LeaderboardNameChange.user_id == current_user.id,
                    LeaderboardNameChange.changed_on >= start,
                    LeaderboardNameChange.changed_on < end,
                    LeaderboardNameChange.previous_name.isnot(None),
                )
            )
            changes_count = int(changes_result.scalar_one())
            if changes_count >= 2:
                raise HTTPException(status_code=400, detail="PSEUDO_CHANGE_LIMIT")
            previous_name = current_user.leaderboard_name
            current_user.leaderboard_name = cleaned
            db.add(
                LeaderboardNameChange(
                    user_id=current_user.id,
                    previous_name=previous_name,
                    new_name=cleaned,
                    changed_on=today,
                )
            )
        else:
            current_user.leaderboard_name = cleaned
    if payload.phone_number is not None:
        current_user.phone_number = payload.phone_number.strip() or None
    if payload.birth_date is not None:
        current_user.birth_date = payload.birth_date
    if payload.country is not None:
        current_user.country = payload.country.strip() or None
    if payload.city is not None:
        current_user.city = payload.city.strip() or None
    if payload.profile_photo_url is not None:
        current_user.profile_photo_url = normalized_profile_photo_url

    await db.commit()
    await db.refresh(current_user)
    return await _build_user_out(db, current_user)


@router.get("/me/export")
async def export_user_data(
    format: str = "json",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    export_format = format.lower()
    if export_format not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="format must be json or csv")

    if export_format == "csv":
        tx_result = await db.execute(
            select(
                Transaction.occurred_on,
                Transaction.type,
                Transaction.amount,
                Transaction.category_id,
                Transaction.description,
            ).where(Transaction.user_id == current_user.id)
        )
        rows = ["date,type,amount,category_id,description"]
        for occurred_on, tx_type, amount, category_id, description in tx_result.all():
            clean_description = (description or "").replace('"', '""')
            rows.append(
                f"{occurred_on},{tx_type},{amount},{category_id},\"{clean_description}\""
            )
        csv_content = "\n".join(rows)
        headers = {"Content-Disposition": "attachment; filename=floussy-data.csv"}
        return Response(content=csv_content, media_type="text/csv", headers=headers)

    envelopes_result = await db.execute(
        select(Envelope).where(Envelope.user_id == current_user.id)
    )
    categories_result = await db.execute(
        select(Category).where(Category.user_id == current_user.id)
    )
    mappings_result = await db.execute(
        select(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id
        )
    )
    transactions_result = await db.execute(
        select(Transaction).where(Transaction.user_id == current_user.id)
    )

    payload = {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "currency": current_user.currency,
            "sweep_interval_days": current_user.sweep_interval_days,
            "first_name": current_user.first_name,
            "last_name": current_user.last_name,
            "phone_number": current_user.phone_number,
            "birth_date": current_user.birth_date.isoformat()
            if current_user.birth_date
            else None,
            "country": current_user.country,
            "city": current_user.city,
            "profile_photo_url": current_user.profile_photo_url,
        },
        "envelopes": [
            {
                "id": str(env.id),
                "name": env.name,
                "rollover_enabled": env.rollover_enabled,
                "is_default_savings": env.is_default_savings,
                "is_cash": env.is_cash,
                "deletable": env.deletable,
            }
            for env in envelopes_result.scalars().all()
        ],
        "categories": [
            {"id": str(cat.id), "name": cat.name}
            for cat in categories_result.scalars().all()
        ],
        "mappings": [
            {
                "category_id": str(mapping.category_id),
                "envelope_id": str(mapping.envelope_id),
            }
            for mapping in mappings_result.scalars().all()
        ],
        "transactions": [
            {
                "id": str(tx.id),
                "type": tx.type.value,
                "category_id": str(tx.category_id),
                "amount": str(tx.amount),
                "occurred_on": tx.occurred_on.isoformat(),
                "description": tx.description,
            }
            for tx in transactions_result.scalars().all()
        ],
    }
    headers = {"Content-Disposition": "attachment; filename=floussy-data.json"}
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers=headers,
    )


@router.post("/me/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_data(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    user_id = current_user.id
    await db.execute(delete(DistributionLog).where(DistributionLog.user_id == user_id))
    await db.execute(delete(DistributionRule).where(DistributionRule.user_id == user_id))
    await db.execute(delete(DistributionRunItem).where(DistributionRunItem.run_id.in_(select(DistributionRun.id).where(DistributionRun.user_id == user_id))))
    await db.execute(delete(DistributionRun).where(DistributionRun.user_id == user_id))
    await db.execute(delete(DistributionItem).where(DistributionItem.user_id == user_id))
    await db.execute(delete(Sweep).where(Sweep.user_id == user_id))
    await db.execute(delete(EnvelopeMovement).where(EnvelopeMovement.user_id == user_id))
    await db.execute(delete(EnvelopeAllocation).where(EnvelopeAllocation.user_id == user_id))
    await db.execute(delete(EnvelopePeriod).where(EnvelopePeriod.user_id == user_id))
    await db.execute(
        delete(EnvelopeTransferLog).where(EnvelopeTransferLog.user_id == user_id)
    )
    await db.execute(
        delete(EnvelopeAdjustmentLog).where(EnvelopeAdjustmentLog.user_id == user_id)
    )
    await db.execute(delete(Goal).where(Goal.user_id == user_id))
    await db.execute(delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user_id))
    await db.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await db.execute(delete(Category).where(Category.user_id == user_id))
    await db.execute(delete(Envelope).where(Envelope.user_id == user_id))

    current_user.next_sweep_date = date.today() + timedelta(
        days=current_user.sweep_interval_days
    )

    default_envelope = Envelope(
        user_id=user_id,
        name="Epargnes",
        is_default_savings=True,
        deletable=False,
        rollover_enabled=True,
    )
    cash_envelope = Envelope(
        user_id=user_id,
        name="Cash",
        is_cash=True,
        is_default_savings=False,
        deletable=False,
        rollover_enabled=False,
    )
    db.add(default_envelope)
    db.add(cash_envelope)
    await db.commit()
