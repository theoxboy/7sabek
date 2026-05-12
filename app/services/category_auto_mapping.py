from __future__ import annotations

import re
from uuid import UUID
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, CategoryEnvelopeMap, Envelope
from app.services.category_catalog import category_key_from_name, is_internal_income_category_key
from app.services.envelope_rules import is_category_mappable_envelope

logger = logging.getLogger(__name__)


_NON_ALNUM = re.compile(r"[^a-z0-9\u0600-\u06ff]+", re.IGNORECASE)


def _norm(value: str) -> str:
    return _NON_ALNUM.sub(" ", value.strip().lower()).strip()


_CATEGORY_GROUP_BY_KEY: dict[str, str] = {
    "rent": "housing",
    "housing_generic": "housing",
    "home_maintenance": "housing",
    "home_insurance": "housing",
    "electricity": "bills",
    "water": "bills",
    "internet": "bills",
    "phone": "bills",
    "gas": "bills",
    "admin_fees": "bills",
    "bills_generic": "bills",
    "groceries": "food",
    "house_supplies": "food",
    "restaurants": "food",
    "health_pharmacy": "health",
    "health_consultation": "health",
    "health_generic": "health",
    "personal_care": "health",
    "transport_public": "transport",
    "transport_taxi": "transport",
    "transport_fuel": "transport",
    "transport_generic": "transport",
    "transport_parking": "transport",
    "transport_maintenance": "transport",
    "car_insurance": "transport",
    "family_support": "family",
    "children_school": "family",
    "children_activities": "family",
    "childcare": "family",
    "debt_payment": "debts",
    "debt_extra_payment": "debts",
    "taxes": "debts",
    "insurance_other": "bills",
    "shopping": "lifestyle",
    "entertainment": "lifestyle",
    "miscellaneous": "lifestyle",
    "subscriptions": "lifestyle",
    "savings_contribution": "flex",
    "investment_contribution": "flex",
    "gifts_charity": "lifestyle",
    "travel": "lifestyle",
    "business_tools": "lifestyle",
    "business_travel": "transport",
    "freelance_expenses": "lifestyle",
}

_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "housing": (
        "loyer",
        "rent",
        "logement",
        "housing",
        "charges",
        "charges logement",
        "الكراء",
        "الكرا",
        "مصاريف السكن",
        "السكن",
    ),
    "bills": (
        "factures",
        "bills",
        "utilities",
        "لفواتير",
        "الفواتير",
        "ضو",
        "الكهرباء",
        "الماء",
        "الما",
        "الانترنت",
        "الإنترنت",
        "التلفون",
        "gas",
        "gaz",
        "الغاز",
    ),
    "food": (
        "courses",
        "food",
        "nourriture",
        "restaurants",
        "restaurant",
        "الماكلة",
        "المأكولات",
        "المطاعم",
    ),
    "health": (
        "sante",
        "santé",
        "health",
        "pharmacie",
        "medical",
        "doctor",
        "الصحة",
        "الصيدلية",
        "الطبيب",
        "العناية الشخصية",
    ),
    "transport": (
        "transport",
        "transport public",
        "public transport",
        "taxi",
        "vtc",
        "carburant",
        "fuel",
        "parking",
        "entretien auto",
        "assurance auto",
        "النقل",
        "النقل العمومي",
        "التنقل",
        "الطاكسي",
        "تاكسي",
        "الوقود",
        "موقف السيارة",
        "صيانة الطوموبيل",
        "تأمين الطوموبيل",
    ),
    "family": (
        "famille",
        "family",
        "aide famille",
        "children",
        "child",
        "school",
        "garde",
        "العائلة",
        "مساعدة العائلة",
        "الدراري",
        "قراية الدراري",
        "حضانة الدراري",
    ),
    "debts": (
        "dettes",
        "dette",
        "debt",
        "credit",
        "crédit",
        "taxes",
        "impots",
        "impôts",
        "الديون",
        "دين",
        "قرض",
        "الضرايب",
    ),
    "lifestyle": (
        "loisirs",
        "shopping",
        "subscriptions",
        "abonnements",
        "cadeaux",
        "dons",
        "travel",
        "voyage",
        "divers",
        "misc",
        "الترفيه",
        "التسوق",
        "الهدايا",
        "السفر",
        "مصاريف متنوعة",
    ),
    "flex": (
        "imprevus",
        "imprévus",
        "urgence",
        "emergency",
        "flex",
        "flexibilite",
        "flexibilité",
        "equilibre",
        "équilibre",
        "balance",
        "الباقي الحر",
        "الطوارئ",
        "المرونة",
        "التوازن",
    ),
}

_DIRECT_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "groceries": ("الماكلة", "المأكولات", "courses", "food", "nourriture"),
    "restaurants": ("المطاعم", "restaurants", "restaurant"),
    "health_pharmacy": ("الصيدلية", "pharmacie", "pharmacy"),
    "health_consultation": ("الطبيب", "consultation", "medical"),
    "transport_public": ("النقل العمومي", "transport public", "public transport"),
    "transport_taxi": ("الطاكسي", "تاكسي", "taxi", "vtc"),
    "transport_fuel": ("الوقود", "carburant", "fuel", "essence"),
    "transport_parking": ("موقف السيارة", "parking"),
    "transport_maintenance": ("صيانة الطوموبيل", "entretien auto", "maintenance"),
    "car_insurance": ("تأمين الطوموبيل", "assurance auto", "car insurance"),
    "debt_payment": ("الديون", "dettes", "debt", "credit", "crédit"),
    "debt_extra_payment": ("الديون", "dettes", "debt", "credit", "crédit"),
    "family_support": ("مساعدة العائلة", "aide famille", "family support"),
    "children_school": ("قراية الدراري", "school", "scolaire", "aide famille"),
    "children_activities": ("أنشطة الدراري", "children activities"),
    "childcare": ("حضانة الدراري", "garde d'enfants", "childcare"),
    "entertainment": ("الترفيه", "loisirs", "entertainment"),
    "shopping": ("التسوق", "shopping"),
}

_GROUP_DEFAULT_ENVELOPE_NAME: dict[str, str] = {
    "family": "Aide famille",
}

_DIRECT_HINT_REQUIRED_PREFIXES: tuple[str, ...] = (
    "debt_",
    "children_",
    "business_",
)

_GENERIC_CATEGORY_KEYS: set[str] = {
    "housing_generic",
    "bills_generic",
    "health_generic",
    "transport_generic",
    "miscellaneous",
}

_GENERIC_ENVELOPE_HINTS_BY_GROUP: dict[str, tuple[str, ...]] = {
    "housing": ("housing", "logement", "مصاريف السكن", "السكن", "charges logement", "charges"),
    "bills": ("factures", "bills", "utilities", "لفواتير", "الفواتير"),
    "health": ("sante", "santé", "health", "الصحة"),
    "transport": ("transport", "النقل", "التنقل"),
    "food": ("food", "nourriture", "الماكلة", "المأكولات"),
    "family": ("family", "famille", "العائلة"),
    "debts": ("debt", "dettes", "credit", "الديون"),
    "lifestyle": ("lifestyle", "loisirs", "الترفيه"),
    "flex": ("flex", "imprevus", "imprévus", "المرونة", "الطوارئ", "التوازن"),
}

_GROUP_FALLBACK_ENVELOPE_HINTS: dict[str, tuple[str, ...]] = {
    "health": ("imprevus", "imprévus", "طوارئ", "التوازن", "المرونة", "balance", "equilibre", "équilibre"),
}


def _direct_hint_score(category_key: str, envelope_name: str) -> float:
    normalized_envelope = _norm(envelope_name)
    if not normalized_envelope:
        return 0.0
    score = 0.0
    for hint in _DIRECT_CATEGORY_HINTS.get(category_key, ()):
        norm_hint = _norm(hint)
        if not norm_hint:
            continue
        if normalized_envelope == norm_hint:
            score = max(score, 100.0)
        elif norm_hint in normalized_envelope:
            score = max(score, 75.0)
    return score


def _requires_direct_hint(category_key: str) -> bool:
    return category_key.startswith(_DIRECT_HINT_REQUIRED_PREFIXES)


def _is_generic_category(category_key: str) -> bool:
    return category_key in _GENERIC_CATEGORY_KEYS


def _infer_envelope_group(envelope_name: str) -> str | None:
    normalized = _norm(envelope_name)
    if not normalized:
        return None
    best_group: str | None = None
    best_score = 0.0
    for group_key, aliases in _GROUP_ALIASES.items():
        score = 0.0
        for alias in aliases:
            norm_alias = _norm(alias)
            if not norm_alias:
                continue
            if normalized == norm_alias:
                score = max(score, 100.0)
            elif norm_alias in normalized:
                score = max(score, 70.0)
        if score > best_score:
            best_score = score
            best_group = group_key
    if best_score < 55.0:
        return None
    return best_group


def _is_group_generic_envelope(group_key: str, envelope_name: str) -> bool:
    normalized = _norm(envelope_name)
    if not normalized:
        return False
    for hint in _GENERIC_ENVELOPE_HINTS_BY_GROUP.get(group_key, ()):
        norm_hint = _norm(hint)
        if not norm_hint:
            continue
        if normalized == norm_hint or norm_hint in normalized:
            return True
    return False


def _pick_group_fallback_envelope(group_key: str, envelopes: list[Envelope]) -> Envelope | None:
    hints = _GROUP_FALLBACK_ENVELOPE_HINTS.get(group_key, ())
    if not hints:
        return None
    best: Envelope | None = None
    for envelope in envelopes:
        normalized_envelope = _norm(envelope.name)
        if not normalized_envelope:
            continue
        for hint in hints:
            norm_hint = _norm(hint)
            if not norm_hint:
                continue
            if normalized_envelope == norm_hint or norm_hint in normalized_envelope:
                return envelope
        if best is None:
            best = envelope
    return None


def _has_generic_group_envelope(group_key: str, envelopes: list[Envelope]) -> bool:
    return any(_is_group_generic_envelope(group_key, envelope.name) for envelope in envelopes)


def _pick_generic_group_envelope(group_key: str, envelopes: list[Envelope]) -> Envelope | None:
    for envelope in envelopes:
        if _is_group_generic_envelope(group_key, envelope.name):
            return envelope
    return None


async def _get_or_create_default_group_envelope(
    db: AsyncSession,
    user_id: UUID,
    category_key: str,
    mappable_envelopes: list[Envelope],
) -> Envelope | None:
    group_key = _CATEGORY_GROUP_BY_KEY.get(category_key)
    default_envelope_name = _GROUP_DEFAULT_ENVELOPE_NAME.get(group_key or "")
    if not default_envelope_name:
        return None
    existing = next(
        (
            envelope
            for envelope in mappable_envelopes
            if _norm(envelope.name) == _norm(default_envelope_name)
        ),
        None,
    )
    if existing is not None:
        return existing
    created = Envelope(
        user_id=user_id,
        name=default_envelope_name,
        rollover_enabled=False,
        is_default_savings=False,
        is_cash=False,
        is_goal=False,
        deletable=True,
    )
    db.add(created)
    await db.flush()
    mappable_envelopes.append(created)
    return created


def _score_envelope_for_category(category_key: str, envelope_name: str) -> float:
    normalized_envelope = _norm(envelope_name)
    if not normalized_envelope:
        return 0.0

    score = _direct_hint_score(category_key, envelope_name)

    if category_key.startswith("debt_"):
        if normalized_envelope.startswith("dettes") or normalized_envelope.startswith("الديون"):
            score = max(score, 95.0)

    group_key = _CATEGORY_GROUP_BY_KEY.get(category_key)
    if group_key:
        for alias in _GROUP_ALIASES.get(group_key, ()):
            norm_alias = _norm(alias)
            if not norm_alias:
                continue
            if normalized_envelope == norm_alias:
                score = max(score, 70.0)
            elif norm_alias in normalized_envelope:
                score = max(score, 55.0)

    return score


def suggest_envelope_for_category_name(
    category_name: str, envelopes: list[Envelope]
) -> Envelope | None:
    category_key = category_key_from_name(category_name)
    group_key = _CATEGORY_GROUP_BY_KEY.get(category_key)
    requires_direct_hint = _requires_direct_hint(category_key)
    generic_category = _is_generic_category(category_key)
    generic_group_exists = bool(group_key) and _has_generic_group_envelope(group_key, envelopes)
    best_score = 0.0
    best_envelope: Envelope | None = None
    best_same_group_score = 0.0
    best_same_group_envelope: Envelope | None = None
    for envelope in envelopes:
        envelope_group = _infer_envelope_group(envelope.name)
        if group_key and envelope_group and envelope_group != group_key:
            continue
        if generic_category and generic_group_exists and group_key and not _is_group_generic_envelope(group_key, envelope.name):
            continue
        candidate_score = _score_envelope_for_category(category_key, envelope.name)
        if requires_direct_hint and _direct_hint_score(category_key, envelope.name) < 75.0:
            continue
        if group_key and envelope_group == group_key:
            candidate_score += 8.0
            if candidate_score > best_same_group_score:
                best_same_group_score = candidate_score
                best_same_group_envelope = envelope
        if generic_category and group_key and _is_group_generic_envelope(group_key, envelope.name):
            candidate_score += 10.0
        if candidate_score > best_score:
            best_score = candidate_score
            best_envelope = envelope
    if best_envelope is None and best_same_group_envelope is not None:
        best_envelope = best_same_group_envelope
        best_score = best_same_group_score
    if best_envelope is None and group_key:
        fallback = _pick_group_fallback_envelope(group_key, envelopes)
        if fallback is not None:
            best_envelope = fallback
            best_score = 62.0
    if best_envelope is None:
        return None
    if best_score < (60.0 if requires_direct_hint else (56.0 if generic_category else 62.0)):
        return None
    return best_envelope


async def fill_missing_category_mappings_for_user(
    db: AsyncSession, user_id: UUID, allow_group_envelope_creation: bool = False
) -> int:
    categories_result = await db.execute(
        select(Category).where(Category.user_id == user_id)
    )
    categories = list(categories_result.scalars().all())

    envelopes_result = await db.execute(
        select(Envelope).where(Envelope.user_id == user_id)
    )
    mappable_envelopes = [
        envelope
        for envelope in envelopes_result.scalars().all()
        if is_category_mappable_envelope(envelope)
    ]
    if not mappable_envelopes:
        return 0

    mappings_result = await db.execute(
        select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user_id)
    )
    mapped_category_ids = {mapping.category_id for mapping in mappings_result.scalars().all()}

    created = 0
    for category in categories:
        if category.id in mapped_category_ids:
            continue
        if is_internal_income_category_key(category.name):
            continue
        suggested = suggest_envelope_for_category_name(category.name, mappable_envelopes)
        if suggested is None and allow_group_envelope_creation:
            category_key = category_key_from_name(category.name)
            suggested = await _get_or_create_default_group_envelope(
                db,
                user_id,
                category_key,
                mappable_envelopes,
            )
        if suggested is None:
            continue
        category_key = category_key_from_name(category.name)
        score = _score_envelope_for_category(category_key, suggested.name)
        logger.info(
            "auto-map-create user=%s category=%s category_key=%s envelope=%s score=%.2f",
            user_id,
            category.name,
            category_key,
            suggested.name,
            score,
        )
        db.add(
            CategoryEnvelopeMap(
                user_id=user_id,
                category_id=category.id,
                envelope_id=suggested.id,
            )
        )
        mapped_category_ids.add(category.id)
        created += 1

    return created


async def reconcile_category_mappings_for_user(
    db: AsyncSession, user_id: UUID, allow_group_envelope_creation: bool = False
) -> tuple[int, int, int]:
    categories_result = await db.execute(
        select(Category).where(Category.user_id == user_id)
    )
    categories = list(categories_result.scalars().all())
    category_by_id = {category.id: category for category in categories}

    envelopes_result = await db.execute(
        select(Envelope).where(Envelope.user_id == user_id)
    )
    mappable_envelopes = [
        envelope
        for envelope in envelopes_result.scalars().all()
        if is_category_mappable_envelope(envelope)
    ]
    envelope_by_id = {envelope.id: envelope for envelope in mappable_envelopes}
    if not mappable_envelopes:
        return (0, 0, 0)

    mappings_result = await db.execute(
        select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user_id)
    )
    mappings = list(mappings_result.scalars().all())
    mapping_by_category_id = {mapping.category_id: mapping for mapping in mappings}

    created = 0
    updated = 0
    deleted_count = 0
    for category in categories:
        if is_internal_income_category_key(category.name):
            continue
        category_key = category_key_from_name(category.name)
        group_key = _CATEGORY_GROUP_BY_KEY.get(category_key)
        generic_category = _is_generic_category(category_key)
        mapping = mapping_by_category_id.get(category.id)
        suggested = suggest_envelope_for_category_name(category.name, mappable_envelopes)
        if suggested is None and allow_group_envelope_creation:
            suggested = await _get_or_create_default_group_envelope(
                db,
                user_id,
                category_key,
                mappable_envelopes,
            )
            if suggested is not None:
                envelope_by_id[suggested.id] = suggested
        if mapping is None:
            if suggested is None:
                continue
            db.add(
                CategoryEnvelopeMap(
                    user_id=user_id,
                    category_id=category.id,
                    envelope_id=suggested.id,
                )
            )
            created += 1
            continue

        current_envelope = envelope_by_id.get(mapping.envelope_id)
        if current_envelope is None:
            if suggested is None:
                await db.execute(
                    delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.id == mapping.id)
                )
                logger.info(
                    "auto-map-delete-invalid-envelope user=%s category=%s",
                    user_id,
                    category.name,
                )
                deleted_count += 1
                continue
            logger.info(
                "auto-map-update-missing-envelope user=%s category=%s envelope=%s",
                user_id,
                category.name,
                suggested.name,
            )
            mapping.envelope_id = suggested.id
            updated += 1
            continue

        current_score = _score_envelope_for_category(category_key, current_envelope.name)
        suggested_score = (
            _score_envelope_for_category(category_key, suggested.name)
            if suggested is not None
            else 0.0
        )
        current_is_generic_group_envelope = (
            bool(group_key) and _is_group_generic_envelope(group_key, current_envelope.name)
        )

        if generic_category and group_key and _has_generic_group_envelope(group_key, mappable_envelopes) and not current_is_generic_group_envelope:
            target = suggested
            if target is None or not _is_group_generic_envelope(group_key, target.name):
                target = _pick_generic_group_envelope(group_key, mappable_envelopes)
            if target is not None and target.id != mapping.envelope_id:
                logger.info(
                    "auto-map-update-generic-to-group user=%s category=%s from=%s to=%s",
                    user_id,
                    category.name,
                    current_envelope.name,
                    target.name,
                )
                mapping.envelope_id = target.id
                updated += 1
            else:
                await db.execute(
                    delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.id == mapping.id)
                )
                logger.info(
                    "auto-map-delete-generic-specialized user=%s category=%s current_envelope=%s",
                    user_id,
                    category.name,
                    current_envelope.name,
                )
                deleted_count += 1
            continue

        if suggested is None:
            if current_score < 45.0:
                await db.execute(
                    delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.id == mapping.id)
                )
                logger.info(
                    "auto-map-delete-low-confidence user=%s category=%s current_envelope=%s current_score=%.2f",
                    user_id,
                    category.name,
                    current_envelope.name,
                    current_score,
                )
                deleted_count += 1
            continue

        if mapping.envelope_id == suggested.id:
            continue

        if suggested_score >= 70.0 and (
            current_score < 50.0 or suggested_score - current_score >= 15.0
        ):
            logger.info(
                "auto-map-update user=%s category=%s from=%s to=%s current_score=%.2f suggested_score=%.2f",
                user_id,
                category.name,
                current_envelope.name,
                suggested.name,
                current_score,
                suggested_score,
            )
            mapping.envelope_id = suggested.id
            updated += 1

    for mapping in mappings:
        category = category_by_id.get(mapping.category_id)
        if category is None:
            continue
        if is_internal_income_category_key(category.name):
            await db.execute(delete(CategoryEnvelopeMap).where(CategoryEnvelopeMap.id == mapping.id))
            logger.info(
                "auto-map-delete-internal-income user=%s category=%s",
                user_id,
                category.name,
            )
            deleted_count += 1

    return (created, updated, deleted_count)
