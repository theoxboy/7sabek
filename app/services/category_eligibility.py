from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, CategoryEnvelopeMap, OnboardingV2Record, Transaction
from app.services.category_catalog import EXPENSE_CATEGORY_KEYS_SQL, category_key_from_name

_ALL_EXPENSE_KEYS = set(EXPENSE_CATEGORY_KEYS_SQL)

_BASE_KEYS = {
    "rent",
    "housing_generic",
    "home_maintenance",
    "electricity",
    "water",
    "internet",
    "phone",
    "gas",
    "home_insurance",
    "admin_fees",
    "bills_generic",
    "groceries",
    "house_supplies",
    "restaurants",
    "health_generic",
    "health_pharmacy",
    "health_consultation",
    "personal_care",
    "transport_public",
    "transport_taxi",
    "transport_generic",
    "miscellaneous",
    "savings_contribution",
    "investment_contribution",
}

_DEBT_KEYS = {"debt_payment", "debt_extra_payment", "taxes"}
_CHILDREN_KEYS = {"children_school", "children_activities", "childcare"}
_VEHICLE_KEYS = {"transport_fuel", "transport_parking", "transport_maintenance", "car_insurance"}
_BUSINESS_KEYS = {"business_tools", "business_travel", "freelance_expenses"}
_LIFESTYLE_KEYS = {"shopping", "entertainment", "subscriptions", "gifts_charity", "travel"}
_FAMILY_SUPPORT_KEYS = {"family_support"}


def _safe_str(value: Any) -> str:
    return str(value).strip().casefold() if isinstance(value, str) else ""


def _safe_num(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _is_yes(value: Any) -> bool:
    normalized = _safe_str(value)
    return normalized in {"yes", "y", "true", "1", "oui", "si", "نعم", "ايه", "أجل"}


def _normalize_income_type(value: Any) -> str:
    normalized = _safe_str(value)
    aliases = {
        "freelancer": "freelancer",
        "freelance": "freelancer",
        "hirafi": "freelancer",
        "independant": "freelancer",
        "independent": "freelancer",
        "self-employed": "freelancer",
        "mixed": "mixed",
        "mixte": "mixed",
        "salary": "salary",
        "salaried": "salary",
        "salaire": "salary",
        "employee": "salary",
    }
    return aliases.get(normalized, normalized)


def _normalize_transport_mode(value: Any) -> str:
    normalized = _safe_str(value)
    aliases = {
        "car": "car",
        "voiture": "car",
        "auto": "car",
        "moto": "moto",
        "bike": "bike",
        "velo": "bike",
        "bicycle": "bike",
        "mixed": "mixed",
        "mixte": "mixed",
        "public": "public",
        "transport_public": "public",
    }
    return aliases.get(normalized, normalized)


def _normalize_lifestyle_level(value: Any) -> str:
    normalized = _safe_str(value)
    aliases = {
        "low": "low",
        "faible": "low",
        "medium": "medium",
        "moyen": "medium",
        "high": "high",
        "eleve": "high",
        "élevé": "high",
    }
    return aliases.get(normalized, normalized)


def derive_profile_signals_from_answers(answers: dict[str, Any]) -> dict[str, bool]:
    income_type = _normalize_income_type(answers.get("Q0_income_type"))

    has_debt = (
        _is_yes(answers.get("E5_has_debt"))
        or _safe_num(answers.get("D1_debt_count")) > 0
    )

    has_children = (
        _is_yes(answers.get("E6_has_children"))
        or _safe_num(answers.get("E6_children_count")) > 0
    )
    has_family_obligation = (
        has_children
        or _is_yes(answers.get("E6_support_family"))
    )

    transport_mode = _normalize_transport_mode(answers.get("E4_transport_mode"))
    has_vehicle = (
        transport_mode in {"car", "mixed", "moto", "bike"}
        or _is_yes(answers.get("TRV0_has_multiple_vehicles"))
        or _safe_str(answers.get("TRX3_detail_mode")) in {"detailed", "detail", "détail"}
    )

    has_business_activity = income_type in {"freelancer", "mixed"}
    lifestyle_level = _normalize_lifestyle_level(answers.get("E7_lifestyle"))
    has_lifestyle = lifestyle_level in {"medium", "high"}

    return {
        "has_debt": has_debt,
        "has_children": has_children,
        "has_family_obligation": has_family_obligation,
        "has_vehicle": has_vehicle,
        "has_business_activity": has_business_activity,
        "has_lifestyle": has_lifestyle,
    }


def eligible_expense_category_keys_from_answers(answers: dict[str, Any]) -> set[str]:
    keys = set(_BASE_KEYS)
    signals = derive_profile_signals_from_answers(answers)
    if signals["has_debt"]:
        keys.update(_DEBT_KEYS)
    if signals["has_children"]:
        keys.update(_CHILDREN_KEYS)
    if signals["has_family_obligation"]:
        keys.update(_FAMILY_SUPPORT_KEYS)
    if signals["has_vehicle"]:
        keys.update(_VEHICLE_KEYS)
    if signals["has_business_activity"]:
        keys.update(_BUSINESS_KEYS)
    if signals["has_lifestyle"]:
        keys.update(_LIFESTYLE_KEYS)
    return keys.intersection(_ALL_EXPENSE_KEYS)


async def latest_onboarding_answers_for_user(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == user_id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None or not isinstance(record.payload, dict):
        return {}
    answers = record.payload.get("answers")
    return dict(answers) if isinstance(answers, dict) else {}


async def prune_ineligible_categories_for_user(
    db: AsyncSession,
    user_id: UUID,
    *,
    eligible_keys: set[str],
) -> int:
    result = await db.execute(select(Category).where(Category.user_id == user_id))
    categories = list(result.scalars().all())
    if not categories:
        return 0

    deletable_ids: list[UUID] = []
    for category in categories:
        key = category_key_from_name(category.name)
        if key in _ALL_EXPENSE_KEYS and key not in eligible_keys:
            deletable_ids.append(category.id)
    if not deletable_ids:
        return 0

    tx_result = await db.execute(
        select(Transaction.category_id).where(
            Transaction.user_id == user_id,
            Transaction.category_id.in_(deletable_ids),
        )
    )
    categories_with_transactions = {
        row[0] for row in tx_result.all() if row and row[0] is not None
    }
    safe_to_delete = [cat_id for cat_id in deletable_ids if cat_id not in categories_with_transactions]
    if not safe_to_delete:
        return 0

    # Delete dependent mappings first to satisfy FK(category_envelope_map.category_id -> categories.id).
    await db.execute(
        delete(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == user_id,
            CategoryEnvelopeMap.category_id.in_(safe_to_delete),
        )
    )
    await db.flush()
    await db.execute(
        delete(Category).where(
            Category.user_id == user_id,
            Category.id.in_(safe_to_delete),
        )
    )

    return len(safe_to_delete)


async def migrate_transacted_ineligible_categories_for_user(
    db: AsyncSession,
    user_id: UUID,
    *,
    eligible_keys: set[str],
) -> int:
    result = await db.execute(select(Category).where(Category.user_id == user_id))
    categories = list(result.scalars().all())
    if not categories:
        return 0

    category_by_key = {category_key_from_name(cat.name): cat for cat in categories}
    migrated = 0

    def _fallback_key(old_key: str) -> str:
        if old_key in _CHILDREN_KEYS or old_key in _FAMILY_SUPPORT_KEYS:
            return "family_support"
        if old_key in _BUSINESS_KEYS:
            return "business_tools"
        if old_key in _VEHICLE_KEYS:
            return "transport_generic"
        if old_key in _DEBT_KEYS:
            return "debt_payment"
        if old_key in _LIFESTYLE_KEYS:
            return "entertainment"
        return "miscellaneous"

    for category in categories:
        old_key = category_key_from_name(category.name)
        if old_key not in _ALL_EXPENSE_KEYS or old_key in eligible_keys:
            continue

        tx_result = await db.execute(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.category_id == category.id,
            )
        )
        transactions = list(tx_result.scalars().all())
        if not transactions:
            continue

        replacement_key = _fallback_key(old_key)
        if replacement_key not in eligible_keys:
            replacement_key = next(iter(sorted(eligible_keys))) if eligible_keys else "miscellaneous"

        replacement = category_by_key.get(replacement_key)
        if replacement is None:
            replacement = Category(user_id=user_id, name=replacement_key)
            db.add(replacement)
            await db.flush()
            category_by_key[replacement_key] = replacement

        for tx in transactions:
            tx.category_id = replacement.id
            migrated += 1

    return migrated
