from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OnboardingV2Record, Transaction, TransactionType, User


def infer_sweep_interval_days_from_answers(answers: dict[str, Any] | None) -> int:
    answers = answers if isinstance(answers, dict) else {}
    income_type = str(answers.get("Q0_income_type") or "").strip()

    if income_type == "salaried":
        frequency = str(answers.get("S3_frequency") or "").strip()
        if frequency == "weekly":
            return 7
        if frequency == "biweekly":
            return 15
        return 30

    if income_type == "mixed":
        primary_cycle = str(answers.get("M2_primary_cycle") or "").strip()
        if primary_cycle == "weekly":
            return 7
        return 30

    if income_type == "hirafi":
        collection_cycle = str(answers.get("H2_collection_cycle") or "").strip()
        if collection_cycle == "weekly":
            return 7
        return 30

    if income_type == "freelancer":
        collection_cycle = str(answers.get("F1b_collection_cycle") or "").strip()
        if collection_cycle == "weekly":
            return 7
        return 30

    return 30


def infer_sweep_cadence_label_from_answers(answers: dict[str, Any] | None) -> str:
    interval = infer_sweep_interval_days_from_answers(answers)
    if interval == 7:
        return "weekly"
    if interval == 15:
        return "biweekly"
    return "monthly"


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _safe_date(value: Any) -> date | None:
    normalized = _safe_string(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _safe_decimal_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    normalized_value = value
    if isinstance(value, str):
        raw = value.strip().replace(" ", "")
        if not raw:
            return None
        has_dot = "." in raw
        has_comma = "," in raw
        cleaned = raw
        if has_dot and has_comma:
            last_dot = raw.rfind(".")
            last_comma = raw.rfind(",")
            decimal_sep = "." if last_dot > last_comma else ","
            thousands_sep = "," if decimal_sep == "." else "."
            cleaned = raw.replace(thousands_sep, "")
            if decimal_sep == ",":
                cleaned = cleaned.replace(",", ".")
        elif has_dot:
            if re.match(r"^-?\d{1,3}(\.\d{3})+([.,]\d+)?$", raw):
                cleaned = raw.replace(".", "")
        elif has_comma:
            if re.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$", raw):
                cleaned = raw.replace(",", "")
            else:
                cleaned = raw.replace(",", ".")
        normalized_value = cleaned
    try:
        parsed = Decimal(str(normalized_value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if parsed <= 0:
        return None
    return str(parsed.quantize(Decimal("0.01")))


def extract_sweep_bootstrap(
    *,
    answers: dict[str, Any] | None,
    draft_objects: dict[str, Any] | None,
) -> dict[str, Any] | None:
    answers = answers if isinstance(answers, dict) else {}
    draft_objects = draft_objects if isinstance(draft_objects, dict) else {}
    payload = (
        draft_objects.get("sweep_bootstrap_v1")
        if isinstance(draft_objects.get("sweep_bootstrap_v1"), dict)
        else {}
    )

    last_income_date = _safe_date(payload.get("last_income_date")) or _safe_date(
        answers.get("SWP1_last_income_date")
    )
    last_income_amount = _safe_decimal_text(
        payload.get("last_income_amount") or answers.get("SWP2_last_income_amount")
    )
    expected_income_amount = _safe_decimal_text(payload.get("expected_income_amount"))
    cadence = _safe_string(payload.get("cadence")) or infer_sweep_cadence_label_from_answers(
        answers
    )
    interval_days = int(payload.get("interval_days") or infer_sweep_interval_days_from_answers(answers))

    if last_income_date is None and last_income_amount is None and expected_income_amount is None:
        return None

    return {
        "last_income_date": last_income_date,
        "last_income_amount": last_income_amount,
        "expected_income_amount": expected_income_amount,
        "cadence": cadence,
        "interval_days": interval_days,
    }


async def get_latest_onboarding_record(
    db: AsyncSession, user_id
) -> OnboardingV2Record | None:
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == user_id)
        .order_by(desc(OnboardingV2Record.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_user_sweep_anchor_date(db: AsyncSession, user: User) -> date:
    record = await get_latest_onboarding_record(db, user.id)
    if record and isinstance(record.payload, dict):
        answers = record.payload.get("answers")
        draft_objects = record.payload.get("draft_objects")
        bootstrap = extract_sweep_bootstrap(
            answers=answers if isinstance(answers, dict) else {},
            draft_objects=draft_objects if isinstance(draft_objects, dict) else {},
        )
        if bootstrap:
            first_income_result = await db.execute(
                select(Transaction.occurred_on)
                .where(
                    Transaction.user_id == user.id,
                    Transaction.type == TransactionType.INCOME,
                    Transaction.created_at >= record.updated_at,
                )
                .order_by(Transaction.created_at.asc())
                .limit(1)
            )
            first_income_occurred_on = first_income_result.scalar_one_or_none()
            if isinstance(first_income_occurred_on, date):
                return first_income_occurred_on
            bootstrap_date = bootstrap.get("last_income_date")
            if isinstance(bootstrap_date, date):
                return bootstrap_date
    return user.created_at.date()


async def build_sweep_bootstrap_status(
    db: AsyncSession, user: User
) -> dict[str, Any] | None:
    record = await get_latest_onboarding_record(db, user.id)
    if record is None or not isinstance(record.payload, dict):
        return None

    answers = record.payload.get("answers")
    draft_objects = record.payload.get("draft_objects")
    bootstrap = extract_sweep_bootstrap(
        answers=answers if isinstance(answers, dict) else {},
        draft_objects=draft_objects if isinstance(draft_objects, dict) else {},
    )
    if not bootstrap:
        return None

    post_onboarding_income_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.INCOME,
            Transaction.created_at >= record.updated_at,
        )
    )
    has_post_onboarding_income = int(post_onboarding_income_result.scalar_one() or 0) > 0

    return {
        "needs_first_income_declaration": not has_post_onboarding_income,
        "last_income_date": bootstrap.get("last_income_date"),
        "last_income_amount": bootstrap.get("last_income_amount"),
        "expected_income_amount": bootstrap.get("expected_income_amount"),
        "cadence": bootstrap.get("cadence"),
        "interval_days": bootstrap.get("interval_days"),
        "onboarding_completed_at": record.updated_at,
    }
