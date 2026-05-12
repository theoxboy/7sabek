from __future__ import annotations

from typing import Any

from app.services.category_catalog import (
    EXPENSE_CATEGORY_CATALOG,
    category_key_from_name,
)


_CATALOG_KEYS = {entry.key for entry in EXPENSE_CATEGORY_CATALOG}

_GROUP_TO_CATEGORY_KEYS: dict[str, list[str]] = {
    "housing": ["rent", "housing_generic", "home_maintenance", "home_insurance"],
    "bills": ["bills_generic", "electricity", "water", "internet", "phone", "gas", "admin_fees"],
    "essentials": ["groceries", "house_supplies", "restaurants"],
    "health": ["health_generic", "health_pharmacy", "health_consultation", "personal_care"],
    "transport": [
        "transport_public",
        "transport_taxi",
        "transport_fuel",
        "transport_parking",
        "transport_maintenance",
        "car_insurance",
        "transport_generic",
    ],
    "family": ["family_support", "children_school", "children_activities", "childcare"],
    "debts": ["debt_payment", "debt_extra_payment", "taxes"],
    "lifestyle": ["shopping", "entertainment", "subscriptions", "gifts_charity", "travel", "miscellaneous"],
    "business": ["business_tools", "business_travel", "freelance_expenses"],
    "saving": ["savings_contribution", "investment_contribution"],
    "buffer": ["miscellaneous", "savings_contribution"],
}

_GROUP_PRIMARY_CATEGORY_KEY: dict[str, str] = {
    "housing": "housing_generic",
    "bills": "bills_generic",
    "essentials": "groceries",
    "health": "health_generic",
    "transport": "transport_generic",
    "family": "family_support",
    "debts": "debt_payment",
    "lifestyle": "entertainment",
    "business": "business_tools",
    "saving": "savings_contribution",
    "buffer": "miscellaneous",
}


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _slug(value: str | None) -> str:
    return (value or "").strip().casefold()


def _infer_group_from_name(name: str) -> str:
    normalized = _slug(name)
    if any(token in normalized for token in ("loyer", "logement", "maison", "sken", "سكن", "كراء", "كرا")):
        return "housing"
    if any(token in normalized for token in ("facture", "internet", "telephone", "ضو", "ماء", "فواتير")):
        return "bills"
    if any(token in normalized for token in ("course", "food", "makla", "ma9la", "ماكلة", "مأكولات", "مطاعم")):
        return "essentials"
    if any(token in normalized for token in ("sante", "santé", "health", "pharm", "medical", "doctor", "صحة", "الصحة", "صيدل", "طبيب", "استشارة طبية")):
        return "health"
    if any(token in normalized for token in ("transport", "carburant", "taxi", "parking", "auto", "نقل", "وقود")):
        return "transport"
    if any(token in normalized for token in ("famille", "children", "school", "garde", "عائلة", "دراري")):
        return "family"
    if any(token in normalized for token in ("dette", "debt", "credit", "دين", "ديون")):
        return "debts"
    if any(token in normalized for token in ("saving", "epargne", "ادخار", "invest")):
        return "saving"
    if any(token in normalized for token in ("freelance", "business", "pro", "travail", "service", "خدمة", "شغل")):
        return "business"
    if any(token in normalized for token in ("buffer", "imprevu", "طوار", "توازن", "مرونة")):
        return "buffer"
    return "lifestyle"


def primary_category_for_envelope(*, envelope_name: str, group_key: str | None) -> str:
    resolved_group = _slug(group_key) if group_key else _infer_group_from_name(envelope_name)
    primary = _GROUP_PRIMARY_CATEGORY_KEY.get(resolved_group, "miscellaneous")
    return primary if primary in _CATALOG_KEYS else "miscellaneous"


def first_eligible_category_for_envelope(
    *,
    envelope_name: str,
    group_key: str | None,
    eligible_keys: set[str],
) -> str | None:
    resolved_group = _slug(group_key) if group_key else _infer_group_from_name(envelope_name)
    for key in _GROUP_TO_CATEGORY_KEYS.get(resolved_group, []):
        if key in eligible_keys and key in _CATALOG_KEYS:
            return key
    primary = primary_category_for_envelope(
        envelope_name=envelope_name,
        group_key=resolved_group,
    )
    if primary in eligible_keys:
        return primary
    return None


def build_system_category_mapping_plan(
    *,
    selected_envelopes: list[dict[str, Any]],
    include_full_group_set: bool = True,
    has_children: bool = False,
    has_business_activity: bool = False,
    has_vehicle: bool = True,
    has_debt: bool = True,
) -> dict[str, str]:
    """
    Return deterministic system-category -> envelope-name mappings.
    For each materialized envelope, ensure at least one mapped system category.
    """
    mapping: dict[str, str] = {}

    for item in selected_envelopes:
        final_name = _safe_string(item.get("final_name")) or _safe_string(item.get("name"))
        if not final_name:
            continue
        group_key = _safe_string(item.get("group_key")) or _infer_group_from_name(final_name)
        group_key = _slug(group_key)

        category_keys = _GROUP_TO_CATEGORY_KEYS.get(group_key, [])
        filtered_keys: list[str] = []
        for key in category_keys:
            if key in {"children_school", "children_activities", "childcare"} and not has_children:
                continue
            if key in {"business_tools", "business_travel", "freelance_expenses"} and not has_business_activity:
                continue
            if key in {"transport_fuel", "transport_parking", "transport_maintenance", "car_insurance"} and not has_vehicle:
                continue
            if key in {"debt_payment", "debt_extra_payment", "taxes"} and not has_debt:
                continue
            filtered_keys.append(key)
        if include_full_group_set and category_keys:
            for key in filtered_keys:
                if key in _CATALOG_KEYS:
                    mapping[key] = final_name
        else:
            primary = _GROUP_PRIMARY_CATEGORY_KEY.get(group_key)
            if primary and primary in _CATALOG_KEYS:
                mapping[primary] = final_name

        custom_category = _safe_string(item.get("custom_category"))
        if custom_category:
            custom_key = category_key_from_name(custom_category)
            if custom_key in _CATALOG_KEYS:
                mapping[custom_key] = final_name

    return mapping
