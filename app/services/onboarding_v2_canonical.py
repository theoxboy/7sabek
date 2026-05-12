from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.services.goals import compute_contribution_amount
from app.services.onboarding_v2_payload_normalization import (
    get_onboarding_answer_list,
    get_onboarding_answer_string,
)
from app.services.category_eligibility import eligible_expense_category_keys_from_answers
from app.services.envelope_virtual import is_virtual_parent_envelope_name
from app.services.sweep_context import infer_sweep_interval_days_from_answers
from app.services.category_catalog import category_key_from_name
from app.services.distribution_name_normalization import distribution_name_equivalent_key

# Legacy front snapshots that used to be read by apply. The backend now either
# recalculates them from answers, treats them as informational-only, or ignores
# them entirely because explicit user actions are persisted in answers.
DRAFT_OBJECTS_RECALCULATED_BACKEND = (
    "distribution_rules",
    "recommended_distribution_rules_v1",
    "contribution_plan_v1",
    "reserve_plan_v1",
    "cycle_normalized_expenses_v1",
    "debt_plan_v2",
    "goal_distribution_rules",
    "distribution_posture_v1",
    "financial_priority_profile",
    "sinking_fund_policy",
    "cash_flow_timing_v1",
    "goals",
    "sinking_funds",
    "categories",
    "mappings",
    "sanity_metrics",
)

DRAFT_OBJECTS_INFORMATIONAL_ONLY = (
    "salary_amount_effects",
    "objective_effects",
    "guidance_direction_v1",
    "salary_notification_beta",
    "onboarding_progress_v2",
)

DRAFT_OBJECTS_VALIDATED_FRONT_ACTIONS = (
    "envelopes_proposal_v1.selected_envelopes",
)

FIXED_ITEMS_LABELS: dict[str, str] = {
    "bills": "Factures",
    "internet_phone": "Internet/Téléphone",
    "utilities": "Eau/Électricité/Gaz",
    "school": "École/Crèche",
    "childcare": "Garde d'enfants",
    "insurance": "Assurance",
    "health": "Santé/Pharmacie",
    "fixed_transport": "Transport fixe",
    "subscriptions": "Abonnements",
    "other": "Autres fixes",
}

DOMAIN_TO_ENVELOPES: dict[str, list[str]] = {
    "rent": ["Loyer"],
    "bills": ["Factures"],
    "food": ["Courses"],
    "transport": ["Transport"],
    "health": ["Santé"],
    "debt": ["Dettes"],
    "savings": ["Épargne"],
    "other": ["Divers"],
}

DOMAIN_TO_CATEGORIES: dict[str, list[str]] = {
    "rent": ["rent"],
    "bills": ["electricity", "water", "internet", "phone", "bills_generic"],
    "food": ["groceries", "restaurants"],
    "transport": ["transport_public", "transport_fuel", "transport_taxi"],
    "health": ["health_generic", "health_pharmacy", "health_consultation"],
    "debt": ["debt_payment", "debt_extra_payment"],
    "savings": ["subscriptions", "miscellaneous"],
    "other": ["entertainment", "shopping", "gifts_charity"],
}

SYSTEM_ENVELOPE_SLUGS = {"cash", "epargne", "epargnes", "savings"}
COMMITMENT_LOCK_GROUPS = {"housing", "transport", "bills", "family", "debts"}
# Structural groups excluded from flexible distribution setup.
# "buffer" and "lifestyle" are intentionally NOT excluded: these child
# envelopes are Morona targets and must receive percentage-based allocations
# when they do not have a fixed amount.
DISTRIBUTION_STRUCTURAL_GROUP_KEYS = {"debts", "goals"}


@dataclass
class ExistingApplyState:
    envelope_names: set[str] = field(default_factory=set)
    goal_names: set[str] = field(default_factory=set)


@dataclass
class CanonicalApplyState:
    selected_envelopes: list[dict[str, Any]] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    mappings: list[dict[str, str]] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)
    sinking_funds: list[dict[str, Any]] = field(default_factory=list)
    debts: list[dict[str, Any]] = field(default_factory=list)
    cycle_normalized_expenses_v1: list[dict[str, Any]] = field(default_factory=list)
    reserve_plan_v1: dict[str, Any] = field(default_factory=dict)
    debt_plan_v2: dict[str, Any] = field(default_factory=dict)
    financial_priority_profile: dict[str, Any] = field(default_factory=dict)
    distribution_posture_v1: dict[str, Any] = field(default_factory=dict)
    sinking_fund_policy: dict[str, Any] = field(default_factory=dict)
    cash_flow_timing_v1: dict[str, Any] = field(default_factory=dict)
    sanity_metrics: dict[str, Any] = field(default_factory=dict)
    debt_posture: str | None = None
    goal_posture: str | None = None
    living_margin_level: str | None = None
    reserve_policy: str | None = None
    reserve_level: str | None = None
    confidence_label: str | None = None
    priority_explanation_lines: list[str] = field(default_factory=list)
    known_amounts_by_envelope: dict[str, float] = field(default_factory=dict)
    distribution_eligible_names: list[str] = field(default_factory=list)


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _safe_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        raw = value.strip().replace(" ", "")
        if not raw:
            return 0.0
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
        value = cleaned
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _round_amount(value: float) -> float:
    return round(value, 2)


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed.quantize(Decimal("0.01"))


def _safe_date(value: Any) -> date | None:
    normalized = _safe_string(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _slugify(value: str) -> str:
    normalized = (
        value.strip()
        .lower()
        .replace("à", "a")
        .replace("á", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("ç", "c")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("ì", "i")
        .replace("í", "i")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ò", "o")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("ú", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ý", "y")
        .replace("ÿ", "y")
        .replace("—", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace("/", " ")
    )
    parts = ["".join(ch for ch in chunk if ch.isalnum()) for chunk in normalized.split()]
    return "_".join(part for part in parts if part)


def _is_system_envelope_name(name: str) -> bool:
    return _slugify(name) in SYSTEM_ENVELOPE_SLUGS


def _unique_names(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        slug = _slugify(value)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        result.append(value.strip())
    return result


def _parse_csv_names(value: str | None) -> list[str]:
    if not value:
        return []
    raw = value.replace("|", ",").replace("\n", ",")
    parts = [part.strip() for part in raw.split(",")]
    return _unique_names([part for part in parts if part])


def _get_answer_string(answers: dict[str, Any], key: str) -> str:
    return get_onboarding_answer_string(answers, key)


def _get_answer_list(answers: dict[str, Any], key: str) -> list[str]:
    return get_onboarding_answer_list(answers, key)


def _get_income_cadence(answers: dict[str, Any]) -> str:
    income_type = _get_answer_string(answers, "Q0_income_type")
    if income_type == "salaried":
        frequency = _get_answer_string(answers, "S3_frequency")
        if frequency in {"weekly", "biweekly"}:
            return frequency
        return "monthly"
    if income_type == "mixed":
        primary_cycle = _get_answer_string(answers, "M2_primary_cycle")
        if primary_cycle == "weekly":
            return "weekly"
        return "monthly"
    if income_type == "hirafi":
        return "weekly" if _get_answer_string(answers, "H2_collection_cycle") == "weekly" else "monthly"
    if income_type == "freelancer":
        return "weekly" if _get_answer_string(answers, "F1b_collection_cycle") == "weekly" else "monthly"
    return "monthly"


def _get_cycles_per_month(answers: dict[str, Any]) -> int:
    cadence = _get_income_cadence(answers)
    if cadence == "weekly":
        return 4
    if cadence == "biweekly":
        return 2
    return 1


def _to_cycle_amount(monthly_amount: float, answers: dict[str, Any]) -> float:
    return _round_amount(monthly_amount / max(_get_cycles_per_month(answers), 1))


def _to_monthly_amount(cycle_amount: float, answers: dict[str, Any]) -> float:
    return _round_amount(cycle_amount * max(_get_cycles_per_month(answers), 1))


def _get_cycle_label(answers: dict[str, Any]) -> str:
    cadence = _get_income_cadence(answers)
    if cadence == "weekly":
        return "فكل أسبوع"
    if cadence == "biweekly":
        return "فكل 15 يوم"
    return "فكل شهر"


def _get_cadence_label(answers: dict[str, Any]) -> str:
    cadence = _get_income_cadence(answers)
    if cadence == "weekly":
        return "أسبوعي"
    if cadence == "biweekly":
        return "كل 15 يوم"
    return "شهري"


def _insurance_cycle_months(value: str) -> int:
    if value == "quarterly":
        return 3
    if value == "semiannual":
        return 6
    if value == "annual":
        return 12
    return 1


def _technical_inspection_cycle_months(value: str) -> int:
    return 24 if value == "biennial" else 12


def _to_monthly_recurring_amount(amount: float, cadence: str) -> float:
    if cadence == "weekly":
        return _round_amount(amount * 4)
    if cadence == "biweekly":
        return _round_amount(amount * 2)
    return _round_amount(amount)


def _normalize_amount_to_monthly(
    amount: float,
    answers: dict[str, Any],
    mode: str,
    cycle_value: str | None = None,
) -> float:
    if amount <= 0:
        return 0.0
    if mode == "income_cadence":
        return _to_monthly_recurring_amount(amount, _get_income_cadence(answers))
    if mode == "insurance_cycle":
        return _round_amount(amount / _insurance_cycle_months(cycle_value or ""))
    if mode == "inspection_cycle":
        return _round_amount(amount / _technical_inspection_cycle_months(cycle_value or ""))
    if mode == "annual_div12":
        return _round_amount(amount / 12)
    return _round_amount(amount)


def _to_monthly_amount_from_fixed_other_cadence(
    amount: float,
    cadence: str,
    answers: dict[str, Any],
) -> float:
    if amount <= 0:
        return 0.0
    if cadence == "income_cadence":
        return _to_monthly_amount(amount, answers)
    if cadence == "weekly":
        return _round_amount(amount * (52 / 12))
    if cadence == "biweekly":
        return _round_amount(amount * (26 / 12))
    if cadence == "quarterly":
        return _round_amount(amount / 3)
    if cadence == "annual":
        return _round_amount(amount / 12)
    return _round_amount(amount)


def _get_fixed_other_rows(answers: dict[str, Any]) -> list[dict[str, Any]]:
    raw = answers.get("FX3_other_fixed_rows")
    if not isinstance(raw, list):
        return []

    result: list[dict[str, Any]] = []
    allowed_cadences = {"income_cadence", "monthly", "weekly", "biweekly", "quarterly", "annual"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _safe_string(item.get("name"))
        amount = _to_number(item.get("amount"))
        cadence_raw = _safe_string(item.get("cadence")) or "income_cadence"
        cadence = cadence_raw if cadence_raw in allowed_cadences else "income_cadence"
        if not name or amount <= 0:
            continue
        result.append({"name": name, "amount": amount, "cadence": cadence})
    return result


def _get_car_maintenance_monthly_amount(answers: dict[str, Any], prefix: str) -> float:
    return _normalize_amount_to_monthly(
        _to_number(answers.get(f"{prefix}car_maintenance_amount")),
        answers,
        "income_cadence",
    )


def _resolve_mixed_transport_detail_target(answers: dict[str, Any]) -> str | None:
    primary_mode = _get_answer_string(answers, "TRX1_primary_mode")
    if primary_mode == "equal":
        return _get_answer_string(answers, "TRX4_equal_detail_target") or None
    return primary_mode or None


def _get_multi_vehicle_count(answers: dict[str, Any]) -> int:
    if _get_answer_string(answers, "TRV0_has_multiple_vehicles") != "yes":
        return 1
    parsed = int(round(_to_number(answers.get("TRV1_vehicle_count")) or 0))
    return min(max(parsed, 2), 4) if parsed > 0 else 2


def _get_car_transport_prefixes(answers: dict[str, Any]) -> list[str]:
    prefixes: set[str] = set()
    transport_mode = _get_answer_string(answers, "E4_transport_mode")
    mixed_detailed = transport_mode == "mixed" and _get_answer_string(answers, "TRX3_detail_mode") == "detailed"
    mixed_target = _resolve_mixed_transport_detail_target(answers)

    if transport_mode == "car":
        if _get_answer_string(answers, "TRV0_has_multiple_vehicles") == "yes":
            for index in range(1, _get_multi_vehicle_count(answers) + 1):
                prefixes.add(f"TRV{index}_")
        else:
            prefixes.add("TR1_")

    if mixed_detailed and mixed_target in {"car_more", "car"}:
        prefixes.add("TRX_C_")

    for key in answers:
        if not isinstance(key, str):
            continue
        if key.startswith("TRV") and "_car_" in key:
            prefixes.add(key.split("car_", 1)[0])

    return sorted(prefixes) or ["TR1_"]


def _get_bike_transport_prefixes(answers: dict[str, Any]) -> list[str]:
    prefixes: set[str] = set()
    transport_mode = _get_answer_string(answers, "E4_transport_mode")
    mixed_detailed = transport_mode == "mixed" and _get_answer_string(answers, "TRX3_detail_mode") == "detailed"
    mixed_target = _resolve_mixed_transport_detail_target(answers)

    if transport_mode == "motorbike":
        if _get_answer_string(answers, "TRV0_has_multiple_vehicles") == "yes":
            for index in range(1, _get_multi_vehicle_count(answers) + 1):
                prefixes.add(f"TRV{index}_")
        else:
            prefixes.add("TRM1_")

    if mixed_detailed and mixed_target in {"bike_more", "bike"}:
        prefixes.add("TRX_B_")

    for key in answers:
        if not isinstance(key, str):
            continue
        if key.startswith("TRV") and "_bike_" in key:
            prefixes.add(key.split("bike_", 1)[0])

    return sorted(prefixes) or ["TRM1_"]


def _get_car_transport_monthly_field_amount(
    answers: dict[str, Any],
    prefix: str,
    field: str,
) -> float:
    amount = _to_number(answers.get(f"{prefix}{field}"))
    if field == "car_maintenance_amount":
        return _normalize_amount_to_monthly(amount, answers, "income_cadence")
    if field == "car_insurance_amount":
        return _normalize_amount_to_monthly(
            amount,
            answers,
            "insurance_cycle",
            _get_answer_string(answers, f"{prefix}car_insurance_cycle"),
        )
    if field == "car_inspection_amount":
        return _normalize_amount_to_monthly(
            amount,
            answers,
            "inspection_cycle",
            _get_answer_string(answers, f"{prefix}car_inspection_cycle"),
        )
    if field == "car_tax_annual_amount":
        return _normalize_amount_to_monthly(amount, answers, "annual_div12")
    return _normalize_amount_to_monthly(amount, answers, "monthly_input")


def _get_car_transport_monthly_total_for_prefix(answers: dict[str, Any], prefix: str) -> float:
    return _round_amount(
        _get_car_transport_monthly_field_amount(answers, prefix, "car_fuel_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_maintenance_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_insurance_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_parking_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_loan_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_inspection_amount")
        + _get_car_transport_monthly_field_amount(answers, prefix, "car_tax_annual_amount")
    )


def _get_bike_transport_monthly_total_for_prefix(answers: dict[str, Any], prefix: str) -> float:
    fuel = _normalize_amount_to_monthly(_to_number(answers.get(f"{prefix}bike_fuel_amount")), answers, "monthly_input")
    insurance = _normalize_amount_to_monthly(
        _to_number(answers.get(f"{prefix}bike_insurance_amount")),
        answers,
        "insurance_cycle",
        _get_answer_string(answers, f"{prefix}bike_insurance_cycle"),
    )
    maintenance = _normalize_amount_to_monthly(
        _to_number(answers.get(f"{prefix}bike_maintenance_amount")),
        answers,
        "monthly_input",
    )
    return _round_amount(fuel + insurance + maintenance)


def _should_use_mixed_transport_total(answers: dict[str, Any]) -> bool:
    if _get_answer_string(answers, "E4_transport_mode") != "mixed":
        return False
    if _get_answer_string(answers, "TRX3_detail_mode") != "detailed":
        return True

    public_total = _to_number(answers.get("TRX_P_public_monthly_amount")) + _to_number(
        answers.get("TRX_P_taxi_monthly_amount")
    )
    car_total = sum(
        _get_car_transport_monthly_total_for_prefix(answers, prefix)
        for prefix in _get_car_transport_prefixes(answers)
        if prefix == "TRX_C_"
    )
    bike_total = sum(
        _get_bike_transport_monthly_total_for_prefix(answers, prefix)
        for prefix in _get_bike_transport_prefixes(answers)
        if prefix == "TRX_B_"
    )
    return public_total + car_total + bike_total <= 0


def _compute_sanity(answers: dict[str, Any]) -> dict[str, Any]:
    income_type = _get_answer_string(answers, "Q0_income_type")
    income_estimate = 0.0
    if income_type == "salaried":
        income_estimate = _to_number(answers.get("S2a_salary_amount"))
    elif income_type == "hirafi":
        income_estimate = _to_number(answers.get("H3_income_profile_min"))
    elif income_type == "freelancer":
        income_estimate = _to_number(answers.get("F7_min_income"))
    elif income_type == "mixed":
        income_estimate = _to_number(answers.get("M3_min_income"))

    fixed_items = _get_answer_list(answers, "FX1_fixed_items")
    fixed_from_setup = 0.0
    for item in fixed_items:
        # "other" is represented by FX3_other_fixed_rows in modern onboarding.
        # Keep legacy field out of sanity to match frontend behavior and avoid double counting.
        if item == "other":
            continue
        fixed_from_setup += _to_number(answers.get(f"FX2_amount_{item}"))
    fixed_other_rows_monthly_total = _round_amount(
        sum(
            _to_monthly_amount_from_fixed_other_cadence(
                float(row.get("amount") or 0.0),
                str(row.get("cadence") or "income_cadence"),
                answers,
            )
            for row in _get_fixed_other_rows(answers)
        )
    )
    family_support_monthly = 0.0
    if _get_answer_string(answers, "E6_support_family") == "yes":
        family_support_monthly = _to_monthly_amount_from_fixed_other_cadence(
            _to_number(answers.get("E6a_support_family_amount")),
            _get_answer_string(answers, "E6b_support_family_cadence") or "monthly",
            answers,
        )
    fixed_rent_amount = _to_number(answers.get("RNT0_rent_amount"))
    housing_loan_amount = _to_number(answers.get("HSN1_loan_monthly_amount"))
    public_transport_amount = _to_number(answers.get("TRP1_public_monthly_amount"))
    public_taxi_amount = _to_number(answers.get("TRP1_taxi_monthly_amount"))
    car_transport_total = sum(
        _get_car_transport_monthly_total_for_prefix(answers, prefix)
        for prefix in _get_car_transport_prefixes(answers)
    )
    bike_transport_total = sum(
        _get_bike_transport_monthly_total_for_prefix(answers, prefix)
        for prefix in _get_bike_transport_prefixes(answers)
    )
    mixed_transport_total = (
        _to_number(answers.get("TRX2_total_monthly_amount")) if _should_use_mixed_transport_total(answers) else 0.0
    )
    essential_load_total = _round_amount(
        fixed_from_setup
        + fixed_other_rows_monthly_total
        + family_support_monthly
        + fixed_rent_amount
        + housing_loan_amount
        + public_transport_amount
        + public_taxi_amount
        + car_transport_total
        + bike_transport_total
        + mixed_transport_total
    )
    remaining = _round_amount(income_estimate - essential_load_total) if income_estimate > 0 else None
    return {
        "incomeEstimate": _round_amount(income_estimate) if income_estimate > 0 else None,
        "fixedTotal": essential_load_total,
        "essentialLoadTotal": essential_load_total,
        "goalsTotalPerIncome": 0.0,
        "remaining": remaining,
    }


def _get_debt_count(answers: dict[str, Any]) -> int:
    value = _get_answer_string(answers, "D1_debt_count")
    if value in {"2", "3"}:
        return int(value)
    inferred = 0
    for index in range(1, 4):
        if any(
            _get_answer_string(answers, key)
            for key in (
                f"D2_debt_name_{index}",
                f"D3_debt_remaining_amount_{index}",
                f"D4_debt_monthly_payment_{index}",
                f"D4_debt_native_amount_{index}",
                f"D4_debt_payment_cadence_{index}",
                f"D1_debt_type_{index}",
                f"D1_debt_status_health_{index}",
            )
        ):
            inferred = index
    return max(1, inferred or 1)


def _get_debt_payment_monthly_factor(cadence: str) -> float:
    if cadence == "weekly":
        return 52 / 12
    if cadence == "biweekly":
        return 26 / 12
    if cadence == "quarterly":
        return 1 / 3
    if cadence == "annual":
        return 1 / 12
    return 1.0


def _get_debt_payment_snapshot(answers: dict[str, Any], index: int) -> dict[str, float | str]:
    native_amount = _to_number(answers.get(f"D4_debt_native_amount_{index}"))
    native_cadence = _get_answer_string(answers, f"D4_debt_payment_cadence_{index}") or "monthly"
    if native_amount > 0:
        monthly_equivalent = _round_amount(native_amount * _get_debt_payment_monthly_factor(native_cadence))
        return {
            "native_amount": native_amount,
            "native_cadence": native_cadence,
            "monthly_equivalent": monthly_equivalent,
            "cycle_equivalent": _to_cycle_amount(monthly_equivalent, answers),
        }

    monthly_fallback = _to_number(answers.get(f"D4_debt_monthly_payment_{index}"))
    return {
        "native_amount": monthly_fallback,
        "native_cadence": "monthly",
        "monthly_equivalent": monthly_fallback,
        "cycle_equivalent": _to_cycle_amount(monthly_fallback, answers),
    }


def _debt_has_target_date(answers: dict[str, Any], index: int) -> bool:
    return _get_answer_string(answers, f"D1_debt_has_target_date_{index}") == "yes" or bool(
        _get_answer_string(answers, f"D5a_debt_target_date_{index}")
    )


def _get_debt_legacy_strategy(answers: dict[str, Any]) -> str:
    preferred = _get_answer_string(answers, "D2_preferred_strategy")
    if preferred == "focus":
        return "focus"
    if preferred in {"balanced", "minimum_only"}:
        return "balanced"
    if _get_answer_string(answers, "P1_debt_priority") == "debt_relief_fast":
        return "focus"
    return "balanced"


def _get_debt_focus_index(answers: dict[str, Any]) -> int:
    preferred = int(_to_number(answers.get("D2_focus_debt_id")))
    if preferred > 0:
        return preferred
    return int(_to_number(answers.get("D7a_focus_debt")))


def _get_cycles_until_future_date(value: str | None, answers: dict[str, Any]) -> int | None:
    target_date = _safe_date(value)
    if target_date is None:
        return None
    delta_days = (target_date - date.today()).days
    if delta_days <= 0:
        return 0
    return max(1, int((delta_days + infer_sweep_interval_days_from_answers(answers) - 1) / infer_sweep_interval_days_from_answers(answers)))


def _build_debt_target_reality_check(answers: dict[str, Any]) -> dict[str, Any]:
    if _get_answer_string(answers, "E5_has_debt") != "yes":
        return {
            "realistic_capacity_per_cycle": None,
            "realistic_capacity_monthly": None,
            "reserve_per_cycle": None,
            "required_per_cycle": None,
            "required_monthly": None,
            "focus_debt_name": None,
            "status": "unknown",
        }

    debt_count = _get_debt_count(answers)
    total_remaining = 0.0
    current_monthly = 0.0
    targeted_debts: list[dict[str, Any]] = []
    for index in range(1, debt_count + 1):
        total_remaining += _to_number(answers.get(f"D3_debt_remaining_amount_{index}"))
        payment = _get_debt_payment_snapshot(answers, index)
        current_monthly += float(payment["monthly_equivalent"])
        target_date = _get_answer_string(answers, f"D5a_debt_target_date_{index}")
        cycles_until_target = _get_cycles_until_future_date(target_date, answers)
        remaining_amount = _to_number(answers.get(f"D3_debt_remaining_amount_{index}"))
        if target_date and cycles_until_target and cycles_until_target > 0 and remaining_amount > 0:
            targeted_debts.append(
                {
                    "name": _get_answer_string(answers, f"D2_debt_name_{index}") or f"Debt {index}",
                    "required_per_cycle": _round_amount(remaining_amount / cycles_until_target),
                    "target_date": target_date,
                }
            )

    sanity = _compute_sanity(answers)
    income_per_cycle = _to_cycle_amount(float(sanity.get("incomeEstimate") or 0), answers)
    fixed_per_cycle = _to_cycle_amount(float(sanity.get("essentialLoadTotal") or 0), answers)
    minimum_debt_monthly = sum(
        float(_get_debt_payment_snapshot(answers, index)["monthly_equivalent"])
        for index in range(1, debt_count + 1)
    )
    minimum_debt_per_cycle = _to_cycle_amount(minimum_debt_monthly, answers)
    remaining_after_core = max(0.0, income_per_cycle - fixed_per_cycle - minimum_debt_per_cycle)
    reserve_per_cycle = _round_amount(remaining_after_core * 0.3)
    realistic_capacity_per_cycle = _round_amount(minimum_debt_per_cycle + remaining_after_core * 0.7)
    required_per_cycle = (
        _round_amount(sum(max(0.0, float(item["required_per_cycle"])) for item in targeted_debts))
        if targeted_debts
        else None
    )
    required_monthly = _to_monthly_amount(required_per_cycle or 0.0, answers) if required_per_cycle is not None else None
    focus_debt_name = None
    if targeted_debts:
        focused = sorted(
            targeted_debts,
            key=lambda item: (float(item["required_per_cycle"]), str(item["target_date"])),
        )[0]
        focus_debt_name = str(focused["name"])

    status = "realistic"
    if required_per_cycle is not None:
        if realistic_capacity_per_cycle <= 0 or required_per_cycle > realistic_capacity_per_cycle * 1.6:
            status = "impossible_now"
        elif required_per_cycle > realistic_capacity_per_cycle * 1.2:
            status = "unrealistic"
        elif required_per_cycle > realistic_capacity_per_cycle:
            status = "ambitious"

    if total_remaining <= 0 or current_monthly <= 0:
        status = "unknown"

    return {
        "realistic_capacity_per_cycle": realistic_capacity_per_cycle or None,
        "realistic_capacity_monthly": _to_monthly_amount(realistic_capacity_per_cycle, answers)
        if realistic_capacity_per_cycle > 0
        else None,
        "reserve_per_cycle": reserve_per_cycle or None,
        "required_per_cycle": required_per_cycle,
        "required_monthly": required_monthly,
        "focus_debt_name": focus_debt_name,
        "status": status,
    }


def _get_suggested_debt_extra_per_cycle(answers: dict[str, Any]) -> float:
    planned_raw = answers.get("F1_guidance_planned_debt")
    has_planned_override = not (
        planned_raw is None
        or (isinstance(planned_raw, str) and not planned_raw.strip())
    )
    if has_planned_override:
        return _round_amount(max(0.0, _to_number(planned_raw)))

    debt_reality = _build_debt_target_reality_check(answers)
    total_minimum_monthly = sum(
        float(_get_debt_payment_snapshot(answers, index)["monthly_equivalent"])
        for index in range(1, _get_debt_count(answers) + 1)
    )
    minimum_per_cycle = _to_cycle_amount(total_minimum_monthly, answers)
    realistic_capacity = float(debt_reality.get("realistic_capacity_per_cycle") or 0.0)
    return _round_amount(max(0.0, realistic_capacity - minimum_per_cycle))


def _get_goal_count(answers: dict[str, Any]) -> int:
    explicit = int(_to_number(answers.get("G1_goal_count")))
    if explicit >= 1:
        return min(5, explicit)
    inferred = 0
    for index in range(1, 6):
        if any(
            _get_answer_string(answers, key)
            for key in (
                f"G1_goal_name_{index}",
                f"G1_goal_type_{index}",
                f"G1_goal_target_amount_{index}",
                f"G1_goal_current_amount_{index}",
                f"G1_goal_target_date_{index}",
            )
        ):
            inferred = index
    return max(1, inferred or 1)


def _goal_has_target_date(answers: dict[str, Any], index: int) -> bool:
    return _get_answer_string(answers, f"G1_goal_has_date_{index}") == "yes" or bool(
        _get_answer_string(answers, f"G1_goal_target_date_{index}")
    )


def _get_goal_entries(answers: dict[str, Any]) -> list[dict[str, Any]]:
    if _get_answer_string(answers, "G0_has_goal") != "yes":
        return []
    entries: list[dict[str, Any]] = []
    for index in range(1, _get_goal_count(answers) + 1):
        name = _get_answer_string(answers, f"G1_goal_name_{index}")
        target_amount = _to_number(answers.get(f"G1_goal_target_amount_{index}"))
        if not name or target_amount <= 0:
            continue
        entries.append(
            {
                "index": index,
                "name": name,
                "goal_type": _get_answer_string(answers, f"G1_goal_type_{index}") or "other",
                "target_amount": _round_amount(target_amount),
                "current_amount": _round_amount(_to_number(answers.get(f"G1_goal_current_amount_{index}"))),
                "target_date": _get_answer_string(answers, f"G1_goal_target_date_{index}") or None,
                "importance": _get_answer_string(answers, f"G1_goal_importance_{index}") or "important",
                "has_target_date": _goal_has_target_date(answers, index),
            }
        )
    return entries


def _get_goal_preview_name(goal_name: str, answers: dict[str, Any], index: int, total_goals: int) -> str:
    normalized_name = goal_name.strip()
    if not normalized_name:
        return f"Objectif {index}" if total_goals > 1 else "Objectif principal"
    return f"Objectif — {normalized_name}"


def _get_goal_importance_priority(value: str) -> int:
    if value == "critical":
        return 1
    if value == "important":
        return 2
    if value == "optional":
        return 3
    return 2


def _get_priority_profile_recommended_mode(answers: dict[str, Any]) -> str:
    has_goal = _get_answer_string(answers, "G0_has_goal") == "yes"
    has_debt = _get_answer_string(answers, "E5_has_debt") == "yes"
    debt_choice = _get_answer_string(answers, "P1_debt_priority")
    goal_choice = _get_answer_string(answers, "P1_goal_priority")
    living_choice = _get_answer_string(answers, "P1_living_priority")
    if has_debt and debt_choice == "debt_relief_fast":
        return "debt_relief_first"
    if has_goal and goal_choice == "goal_start_now" and (not has_debt or debt_choice != "debt_relief_fast"):
        return "goal_growth_first"
    if living_choice == "living_comfort" and (not has_debt or debt_choice != "debt_relief_fast"):
        return "stability_first"
    return "balanced_rebuild"


def _get_priority_profile_living_margin_level(answers: dict[str, Any]) -> str:
    living_choice = _get_answer_string(answers, "P1_living_priority")
    if living_choice == "living_comfort":
        return "high"
    if living_choice == "living_tight":
        return "low"
    return "medium"


def _get_priority_profile_reserve_policy(answers: dict[str, Any]) -> str:
    mode = _get_priority_profile_recommended_mode(answers)
    if mode == "stability_first":
        return "balanced_reserve"
    if mode == "debt_relief_first":
        return "minimal_reserve"
    return "starter_first"


def _get_priority_profile_debt_posture(answers: dict[str, Any]) -> str:
    has_debt = _get_answer_string(answers, "E5_has_debt") == "yes"
    if not has_debt:
        return "minimum_only"
    choice = _get_answer_string(answers, "P1_debt_priority")
    if choice == "debt_relief_fast":
        return "focus"
    if choice == "debt_under_control":
        return "minimum_only"
    return "balanced"


def _get_priority_profile_goal_posture(answers: dict[str, Any]) -> str:
    has_goal = _get_answer_string(answers, "G0_has_goal") == "yes"
    if not has_goal:
        return "later"
    choice = _get_answer_string(answers, "P1_goal_priority")
    if choice == "goal_start_now":
        return "now"
    if choice == "goal_start_light":
        return "light"
    return "later"


def _get_priority_profile_reserve_level(answers: dict[str, Any]) -> str:
    policy = _get_priority_profile_reserve_policy(answers)
    if policy == "balanced_reserve":
        return "high"
    if policy == "minimal_reserve":
        return "low"
    return "medium"


def _get_priority_profile_confidence_label(answers: dict[str, Any]) -> str:
    mode = _get_priority_profile_recommended_mode(answers)
    if mode == "stability_first":
        return "comfortable"
    if mode == "debt_relief_first":
        return "tight"
    return "balanced"


def _get_priority_profile_summary_lines(answers: dict[str, Any]) -> list[str]:
    mode = _get_priority_profile_recommended_mode(answers)
    if mode == "debt_relief_first":
        return [
            "حمينا الضروريات وخفضنا الضغط على الحوايج الثانوية.",
            "خلّينا احتياط صغير حاضر باش ما ترجعش أي مفاجأة تزيد عليك.",
            "الزيادة اللي غادي تبقى غادي تمشي للدين اللي محتاج النفس الأول.",
        ]
    if mode == "goal_growth_first":
        return [
            "حمينا المعيشة والالتزامات اللي ما خاصهاش تسقط.",
            "خلّينا احتياط حاضر وبداية واضحة للهدف من دابا.",
            "الديون غادي يبقاو تحت السيطرة بلا ما يتخلطو مع الهدف.",
        ]
    if mode == "stability_first":
        return [
            "الأولوية الأولى غادي تبقى للمعيشة والاستقرار اليومي.",
            "الاحتياط غادي يكون حاضر بنسبة أقوى قبل أي ضغط إضافي.",
            "الديون والأهداف غادي يتحركو بخطوات أخف حتى يوقف الحساب مزيان.",
        ]
    return [
        "حمينا الضروريات والحد الأدنى ديال الالتزامات.",
        "خلّينا احتياط متوسط باش الخطة تبقى واقعية.",
        "غادي نوزعو الجهد بين الديون، الهدف، والمعيشة بتوازن.",
    ]


def _get_goal_contribution_weight(
    answers: dict[str, Any],
    *,
    index: int,
    goal_intent: str,
    focus_goal_index: int,
) -> float:
    importance = _get_answer_string(answers, f"G1_goal_importance_{index}") or "important"
    importance_weight = 1.5 if importance == "critical" else 1.15 if importance == "important" else 0.85 if importance == "idea" else 0.6
    started_weight = 0.15 if _to_number(answers.get(f"G1_goal_current_amount_{index}")) > 0 else 0.0
    dated_weight = 0.2 if _goal_has_target_date(answers, index) else 0.0
    urgent_ids = {int(_to_number(value)) for value in _get_answer_list(answers, "G2_targeted_goal_ids")}
    urgent_weight = 0.25 if index in urgent_ids else 0.0
    focus_weight = 0.65 if focus_goal_index == index else 0.0
    intent_weight = 1.0 if goal_intent == "start_now" else 0.85 if goal_intent == "start_light" else 0.65
    return importance_weight + started_weight + dated_weight + urgent_weight + focus_weight + intent_weight


def _build_canonical_goals(answers: dict[str, Any]) -> list[dict[str, Any]]:
    goal_entries = _get_goal_entries(answers)
    if not goal_entries:
        return []

    total_goal_contribution_per_cycle = _round_amount(_to_number(answers.get("F1_guidance_planned_goals")))
    goal_intent = _get_answer_string(answers, "G2_goal_intent") or "start_light"
    focus_goal_index = int(_to_number(answers.get("G2_focus_goal_id")))
    total_weight = sum(
        _get_goal_contribution_weight(
            answers,
            index=int(entry["index"]),
            goal_intent=goal_intent,
            focus_goal_index=focus_goal_index,
        )
        for entry in goal_entries
    )

    goals: list[dict[str, Any]] = []
    for offset, entry in enumerate(goal_entries, start=1):
        if total_goal_contribution_per_cycle > 0 and total_weight > 0:
            contribution_amount = _round_amount(
                total_goal_contribution_per_cycle
                * _get_goal_contribution_weight(
                    answers,
                    index=int(entry["index"]),
                    goal_intent=goal_intent,
                    focus_goal_index=focus_goal_index,
                )
                / total_weight
            )
        else:
            contribution_amount = _round_amount(
                float(
                    compute_contribution_amount(
                        Decimal(str(entry["target_amount"])),
                        _safe_date(entry["target_date"]),
                        infer_sweep_interval_days_from_answers(answers),
                    )
                    or Decimal("0.00")
                )
            )
        goals.append(
            {
                "name": entry["name"],
                "envelope_name": _get_goal_preview_name(
                    str(entry["name"]),
                    answers,
                    offset,
                    len(goal_entries),
                ),
                "goal_type": "goal",
                "target_amount": entry["target_amount"],
                "current_amount": entry["current_amount"],
                "priority": _get_goal_importance_priority(str(entry["importance"])),
                "target_date": entry["target_date"],
                "contribution_amount": contribution_amount,
                "auto_contribute": contribution_amount > 0,
            }
        )
    return goals


def _build_canonical_debts(answers: dict[str, Any]) -> list[dict[str, Any]]:
    if _get_answer_string(answers, "E5_has_debt") != "yes":
        return []
    debt_strategy = _get_debt_legacy_strategy(answers)
    debt_focus_index = _get_debt_focus_index(answers)
    debt_reality = _build_debt_target_reality_check(answers)
    debts: list[dict[str, Any]] = []
    for index in range(1, _get_debt_count(answers) + 1):
        payment = _get_debt_payment_snapshot(answers, index)
        name = _get_answer_string(answers, f"D2_debt_name_{index}") or f"Debt {index}"
        is_focus = debt_strategy != "balanced" and (
            debt_focus_index == index
            or (debt_focus_index == 0 and debt_reality.get("focus_debt_name") == name)
        )
        debts.append(
            {
                "index": index,
                "name": name,
                "envelope_name": f"Dettes — {name}",
                "remaining_amount": _round_amount(_to_number(answers.get(f"D3_debt_remaining_amount_{index}"))),
                "monthly_payment": float(payment["monthly_equivalent"]),
                "payment_per_cycle": float(payment["cycle_equivalent"]),
                "target_date": _get_answer_string(answers, f"D5a_debt_target_date_{index}") or None,
                "strategy": "balanced" if debt_strategy == "balanced" else "focus_primary" if is_focus else "minimum_only",
                "priority": 2 if debt_strategy == "balanced" else 1 if is_focus else 3,
            }
        )
    return debts


def _build_fixed_items(answers: dict[str, Any]) -> list[dict[str, Any]]:
    fixed_item_envelope_map = {
        "bills": "Factures",
        "internet_phone": "Internet/Téléphone",
        "utilities": "Factures",
        "school": "École/Crèche",
        "childcare": "École/Crèche",
        "insurance": "Assurance",
        "health": "Santé",
        "fixed_transport": "Transport fixe",
        "subscriptions": "Autres fixes",
        "other": "Autres fixes",
    }
    fixed_items: list[dict[str, Any]] = []
    for item in _get_answer_list(answers, "FX1_fixed_items"):
        amount = _round_amount(_to_number(answers.get(f"FX2_amount_{item}")))
        if amount <= 0:
            continue
        fixed_items.append(
            {
                "key": item,
                "label": FIXED_ITEMS_LABELS.get(item, item),
                "amount": amount,
                "envelope": fixed_item_envelope_map.get(item, ""),
            }
        )

    for index, row in enumerate(_get_fixed_other_rows(answers), start=1):
        monthly_amount = _to_monthly_amount_from_fixed_other_cadence(
            float(row.get("amount") or 0.0),
            str(row.get("cadence") or "income_cadence"),
            answers,
        )
        if monthly_amount <= 0:
            continue
        name = _safe_string(row.get("name")) or f"Autre fixe {index}"
        fixed_items.append(
            {
                "key": f"fixed_other_{index}",
                "label": name,
                "amount": monthly_amount,
                "envelope": name,
            }
        )

    if _get_answer_string(answers, "E6_support_family") == "yes":
        support_amount = _to_number(answers.get("E6a_support_family_amount"))
        cadence = _get_answer_string(answers, "E6b_support_family_cadence") or "monthly"
        support_monthly = _to_monthly_amount_from_fixed_other_cadence(support_amount, cadence, answers)
        if support_monthly > 0:
            fixed_items.append(
                {
                    "key": "family_support_auto",
                    "label": "Aide famille",
                    "amount": support_monthly,
                    "envelope": "Aide famille",
                }
            )

    rent_amount = _to_number(answers.get("RNT0_rent_amount"))
    if rent_amount > 0:
        fixed_items.append({"key": "rent_auto", "label": "Loyer", "amount": _round_amount(rent_amount), "envelope": "Loyer"})

    housing_loan_amount = _to_number(answers.get("HSN1_loan_monthly_amount"))
    if housing_loan_amount > 0:
        fixed_items.append(
            {
                "key": "housing_loan_auto",
                "label": "Crédit logement",
                "amount": _round_amount(housing_loan_amount),
                "envelope": "Loyer",
            }
        )

    public_transport_amount = _to_number(answers.get("TRP1_public_monthly_amount"))
    if public_transport_amount > 0:
        fixed_items.append(
            {
                "key": "public_transport_auto",
                "label": "Transport public",
                "amount": _round_amount(public_transport_amount),
                "envelope": "Transport",
            }
        )

    public_taxi_amount = _to_number(answers.get("TRP1_taxi_monthly_amount"))
    if public_taxi_amount > 0:
        fixed_items.append({"key": "taxi_auto", "label": "Taxi / VTC", "amount": _round_amount(public_taxi_amount), "envelope": "Transport"})

    for index, prefix in enumerate(_get_car_transport_prefixes(answers), start=1):
        suffix = f"_{index}" if len(_get_car_transport_prefixes(answers)) > 1 else ""
        car_fuel = _get_car_transport_monthly_field_amount(answers, prefix, "car_fuel_amount")
        if car_fuel > 0:
            fixed_items.append({"key": f"car_fuel_auto{suffix}", "label": "Carburant", "amount": car_fuel, "envelope": "Transport"})
        car_maintenance = _get_car_transport_monthly_field_amount(answers, prefix, "car_maintenance_amount")
        if car_maintenance > 0:
            fixed_items.append({"key": f"car_maintenance_auto{suffix}", "label": "Entretien auto", "amount": car_maintenance, "envelope": "Transport"})
        car_insurance = _get_car_transport_monthly_field_amount(answers, prefix, "car_insurance_amount")
        if car_insurance > 0:
            fixed_items.append({"key": f"car_insurance_auto{suffix}", "label": "Assurance auto", "amount": car_insurance, "envelope": "Transport"})
        car_parking = _get_car_transport_monthly_field_amount(answers, prefix, "car_parking_amount")
        if car_parking > 0:
            fixed_items.append({"key": f"car_parking_auto{suffix}", "label": "Parking", "amount": car_parking, "envelope": "Transport"})
        car_loan = _get_car_transport_monthly_field_amount(answers, prefix, "car_loan_amount")
        if car_loan > 0:
            fixed_items.append({"key": f"car_loan_auto{suffix}", "label": "Crédit auto", "amount": car_loan, "envelope": "Dettes"})
        car_inspection = _get_car_transport_monthly_field_amount(answers, prefix, "car_inspection_amount")
        if car_inspection > 0:
            fixed_items.append({"key": f"car_inspection_auto{suffix}", "label": "Contrôle technique", "amount": car_inspection, "envelope": "Transport"})
        car_tax = _get_car_transport_monthly_field_amount(answers, prefix, "car_tax_annual_amount")
        if car_tax > 0:
            fixed_items.append({"key": f"car_tax_auto{suffix}", "label": "Taxe auto", "amount": car_tax, "envelope": "Transport"})

    for index, prefix in enumerate(_get_bike_transport_prefixes(answers), start=1):
        suffix = f"_{index}" if len(_get_bike_transport_prefixes(answers)) > 1 else ""
        bike_fuel = _normalize_amount_to_monthly(_to_number(answers.get(f"{prefix}bike_fuel_amount")), answers, "monthly_input")
        if bike_fuel > 0:
            fixed_items.append({"key": f"bike_fuel_auto{suffix}", "label": "Carburant 2 roues", "amount": bike_fuel, "envelope": "Transport"})
        bike_insurance = _normalize_amount_to_monthly(
            _to_number(answers.get(f"{prefix}bike_insurance_amount")),
            answers,
            "insurance_cycle",
            _get_answer_string(answers, f"{prefix}bike_insurance_cycle"),
        )
        if bike_insurance > 0:
            fixed_items.append({"key": f"bike_insurance_auto{suffix}", "label": "Assurance 2 roues", "amount": bike_insurance, "envelope": "Transport"})
        bike_maintenance = _normalize_amount_to_monthly(_to_number(answers.get(f"{prefix}bike_maintenance_amount")), answers, "monthly_input")
        if bike_maintenance > 0:
            fixed_items.append({"key": f"bike_maintenance_auto{suffix}", "label": "Entretien 2 roues", "amount": bike_maintenance, "envelope": "Transport"})

    return fixed_items


def _get_expense_priority_layer(item: dict[str, Any]) -> tuple[str, str]:
    source = " ".join(str(item.get(key) or "").lower() for key in ("key", "label", "envelope"))
    if any(token in source for token in ("loyer", "loan", "credit", "crédit", "carburant", "transport", "sant")):
        return "protected", "مصروف أساسي أو التزام ما خاصوش يتعطل."
    if any(token in source for token in ("insurance", "assurance", "tax", "taxe", "inspection", "visite", "school", "ecole")):
        return "planned_future_obligation", "مصروف مستقبلي معروف من قبل، خاصو يتجمع بشوية على كل دورة."
    return "scheduled", "مصروف متكرر خاصو يبقى حاضر ولكن ماشي بنفس خطورة الضروريات."


def _derive_selected_domains(answers: dict[str, Any]) -> list[str]:
    from_fixed = []
    for item in _get_answer_list(answers, "FX1_fixed_items"):
        if item == "internet_phone":
            from_fixed.append("bills")
        elif item == "loan":
            from_fixed.append("debt")
        elif item == "school":
            from_fixed.append("other")
        elif item == "insurance":
            from_fixed.append("bills")
        elif item == "fixed_transport":
            from_fixed.append("transport")
        else:
            from_fixed.append(item)

    heuristics = []
    if _get_answer_string(answers, "E3_housing_status"):
        heuristics.extend(["rent", "bills"])
    if _get_answer_string(answers, "E4_transport_mode"):
        heuristics.append("transport")
    heuristics.extend(["food", "health", "savings"])
    if _get_answer_string(answers, "E5_has_debt") == "yes":
        heuristics.append("debt")
    if _get_answer_string(answers, "E7_lifestyle") in {"medium", "high"}:
        heuristics.append("other")
    return _unique_names([*heuristics, *from_fixed])


def _derive_categories(answers: dict[str, Any]) -> list[str]:
    eligible = sorted(eligible_expense_category_keys_from_answers(answers))
    if not eligible:
        return _unique_names(["groceries", "transport_public", "bills_generic"])
    return _unique_names(eligible)


def _default_envelope_for_category(category: str, selected_envelopes: list[dict[str, Any]]) -> str | None:
    category_key = category_key_from_name(category)
    normalized = category_key.casefold()
    if normalized in {"rent", "housing_generic", "home_maintenance", "home_insurance"}:
        return "Loyer"
    if normalized in {"electricity", "water", "internet", "phone", "bills_generic", "gas", "admin_fees"}:
        return "Factures"
    if normalized in {"groceries", "restaurants", "house_supplies"}:
        return "Courses"
    if normalized.startswith("transport_") or normalized == "transport_generic":
        return "Transport"
    if normalized.startswith("health_") or normalized == "health_generic":
        return "Santé"
    if normalized.startswith("debt_"):
        return "Dettes"
    if normalized in {"miscellaneous", "subscriptions", "insurance_other"}:
        return "Épargne"
    for item in selected_envelopes:
        final_name = _safe_string(item.get("final_name"))
        if final_name and not _is_system_envelope_name(final_name):
            return final_name
    return None


def _get_proposal_group_for_category_name(category: str) -> str | None:
    category_key = category_key_from_name(category)
    normalized = _slugify(category_key)
    if normalized in {"rent", "housing_generic", "home_maintenance", "home_insurance"}:
        return "housing"
    if normalized in {"electricity", "water", "internet", "phone", "bills_generic", "gas", "admin_fees"}:
        return "bills"
    if normalized in {"groceries", "restaurants", "house_supplies", "health_generic", "health_pharmacy", "health_consultation", "personal_care"}:
        return "essentials"
    if normalized.startswith("transport_") or normalized == "transport_generic":
        return "transport"
    if normalized.startswith("debt_") or normalized == "taxes":
        return "debts"
    if normalized in {"family_support", "children_school", "children_activities", "childcare"}:
        return "family"
    if normalized in {"travel", "business_tools", "business_travel", "freelance_expenses", "shopping", "entertainment", "gifts_charity", "miscellaneous"}:
        return "lifestyle"
    if any(token in normalized for token in ("loyer", "logement", "charge", "maison")):
        return "housing"
    if any(token in normalized for token in ("electric", "eau", "internet", "telephone", "telephon", "facture", "assurance")):
        return "bills"
    if any(token in normalized for token in ("course", "restaurant", "nourriture", "epicer", "makla", "sante", "pharm", "medic", "clinique", "soin", "dentaire", "lunette")):
        return "essentials"
    if any(token in normalized for token in ("transport", "carburant", "parking", "taxi", "moto", "velo")):
        return "transport"
    if any(token in normalized for token in ("credit", "rembourse", "dette")):
        return "debts"
    if any(token in normalized for token in ("ecole", "hadana", "fourniture", "cours", "famille")):
        return "family"
    if any(token in normalized for token in ("epargne", "invest")):
        return "saving"
    if any(token in normalized for token in ("urgence", "imprevu", "imprevus", "buffer")):
        return "buffer"
    if any(token in normalized for token in ("objectif", "goal")):
        return "goals"
    if any(token in normalized for token in ("loisir", "divers", "shopping")):
        return "lifestyle"
    return None


def _resolve_selected_envelope_name_for_category(
    category: str,
    selected_envelopes: list[dict[str, Any]],
    fallback_envelope_name: str | None,
) -> str | None:
    candidates = [
        item
        for item in selected_envelopes
        if _safe_string(item.get("final_name")) and not _is_system_envelope_name(str(item.get("final_name")))
    ]
    if not candidates:
        return None

    normalized_fallback = _slugify(fallback_envelope_name) if fallback_envelope_name else None
    if normalized_fallback:
        for item in candidates:
            final_name = _safe_string(item.get("final_name"))
            name = _safe_string(item.get("name"))
            if final_name and _slugify(final_name) == normalized_fallback:
                return final_name
            if name and _slugify(name) == normalized_fallback:
                return final_name or name

    group_key = _get_proposal_group_for_category_name(category)
    if group_key:
        for item in candidates:
            if _safe_string(item.get("group_key")) == group_key:
                return _safe_string(item.get("final_name")) or _safe_string(item.get("name"))

    return _safe_string(candidates[0].get("final_name")) or _safe_string(candidates[0].get("name"))


def _infer_selected_envelope_group(name: str) -> str:
    normalized = _slugify(name)
    if normalized.startswith("objectif") or "objectif" in normalized or "goal" in normalized:
        return "goals"
    if normalized.startswith("dettes") or normalized.startswith("dette") or "credit" in normalized:
        return "debts"
    if any(token in normalized for token in ("loyer", "logement", "maison", "charge")):
        return "housing"
    if any(token in normalized for token in ("facture", "internet", "telephone", "assurance")):
        return "bills"
    if any(token in normalized for token in ("course", "restaurant", "sante", "pharmacie")):
        return "essentials"
    if any(token in normalized for token in ("transport", "taxi", "carburant", "parking", "auto", "roues", "velo")):
        return "transport"
    if any(token in normalized for token in ("famille", "enfants", "ecole", "creche", "garde")):
        return "family"
    if any(token in normalized for token in ("epargne", "saving", "savings", "invest")):
        return "saving"
    if any(
        token in normalized
        for token in ("urgence", "imprevu", "buffer", "tawari", "طوار", "توازن", "balance")
    ):
        return "buffer"
    if any(token in normalized for token in ("loisirs", "shopping", "sorties", "voyage", "cadeaux")):
        return "lifestyle"
    return "essentials"


def _extract_explicit_selected_envelopes(answers: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = _safe_dict_list(answers.get("E11_selected_envelopes_v1"))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in explicit:
        name = _safe_string(item.get("name"))
        final_name = _safe_string(item.get("final_name")) or name
        if not final_name or _is_system_envelope_name(final_name):
            continue
        slug = _slugify(final_name)
        if slug in seen:
            continue
        seen.add(slug)
        result.append(
            {
                "name": name or final_name,
                "final_name": final_name,
                "group_key": _safe_string(item.get("group_key")) or _infer_selected_envelope_group(final_name),
                "final_rollover_enabled": bool(item.get("final_rollover_enabled")),
                "custom_category": _safe_string(item.get("custom_category")),
                "custom_amount": _to_number(item.get("custom_amount")) or None,
            }
        )
    return result


def _derive_selected_envelopes_legacy(
    answers: dict[str, Any],
    *,
    goals: list[dict[str, Any]],
    debts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_slugs_in_order: list[str] = []
    seen_selected_slugs: set[str] = set()
    for item in _get_answer_list(answers, "E10_keep_suggestions"):
        slug = _slugify(item)
        if not slug or slug in seen_selected_slugs:
            continue
        seen_selected_slugs.add(slug)
        selected_slugs_in_order.append(slug)

    custom_names = _parse_csv_names(_get_answer_string(answers, "C1_custom_envelopes"))
    candidate_names: list[str] = [
        *custom_names,
        *[item["envelope_name"] for item in goals if _safe_string(item.get("envelope_name"))],
        *[item["envelope_name"] for item in debts if _safe_string(item.get("envelope_name"))],
    ]
    for values in DOMAIN_TO_ENVELOPES.values():
        candidate_names.extend(values)
    candidate_names.extend(
        [
            "Courses",
            "Santé",
            "Loyer",
            "Factures",
            "Transport",
            "Dettes",
            "Divers",
            "École/Crèche",
            "Internet/Téléphone",
            "Assurance",
            "Transport fixe",
            "Autres fixes",
            "Taxi / VTC",
            "Carburant",
            "Assurance auto",
            "Entretien auto",
            "Parking",
            "Crédit auto",
            "Contrôle technique",
            "Taxe auto",
            "Carburant 2 roues",
            "Assurance 2 roues",
            "Entretien 2 roues",
        ]
    )

    slug_to_candidate_name: dict[str, str] = {}
    for name in _unique_names(candidate_names):
        slug = _slugify(name)
        if slug and slug not in slug_to_candidate_name:
            slug_to_candidate_name[slug] = name

    selected_names = [
        slug_to_candidate_name[slug]
        for slug in selected_slugs_in_order
        if slug in slug_to_candidate_name
    ]
    if not selected_names:
        fallback_names: list[str] = []
        for domain in _derive_selected_domains(answers):
            fallback_names.extend(DOMAIN_TO_ENVELOPES.get(domain, []))
        fallback_names.extend(custom_names)
        selected_names = _unique_names(fallback_names or ["Courses", "Transport", "Factures"])

    return [
        {
            "name": name,
            "final_name": name,
            "group_key": _infer_selected_envelope_group(name),
            "final_rollover_enabled": _infer_selected_envelope_group(name) in {"goals", "saving", "buffer"},
            "custom_category": None,
            "custom_amount": None,
        }
        for name in _unique_names(selected_names)
        if not _is_system_envelope_name(name)
    ]


def _build_cycle_normalized_expenses(answers: dict[str, Any], fixed_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cycle_label = _get_cycle_label(answers)
    cadence_label = _get_cadence_label(answers)
    items: list[dict[str, Any]] = []
    for item in fixed_items:
        layer, reason = _get_expense_priority_layer(item)
        monthly_amount = _round_amount(float(item.get("amount") or 0.0))
        items.append(
            {
                "key": item.get("key"),
                "label": item.get("label"),
                "envelope": item.get("envelope"),
                "monthly_amount": monthly_amount,
                "per_cycle_amount": _to_cycle_amount(monthly_amount, answers),
                "cycle_label": cycle_label,
                "cadence_label": cadence_label,
                "priority_layer": layer,
                "layer_reason": reason,
            }
        )
    return items


def _build_sinking_funds(cycle_normalized_expenses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in cycle_normalized_expenses
        if item.get("priority_layer") == "planned_future_obligation"
    ]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        monthly_amount = _round_amount(float(item.get("monthly_amount") or 0.0))
        per_cycle_amount = _round_amount(float(item.get("per_cycle_amount") or 0.0))
        label = _safe_string(item.get("label"))
        envelope_name = _safe_string(item.get("envelope"))
        if not label or not envelope_name:
            continue
        result.append(
            {
                "name": label,
                "envelope_name": envelope_name,
                "goal_type": "sinking_fund",
                "target_amount": _round_amount(monthly_amount * 12),
                "contribution_amount": per_cycle_amount,
                "auto_contribute": True,
                "priority": index,
            }
        )
    return result


def _build_known_amounts_by_envelope(
    *,
    cycle_normalized_expenses: list[dict[str, Any]],
    debts: list[dict[str, Any]],
) -> dict[str, float]:
    amounts: dict[str, float] = {}
    for item in cycle_normalized_expenses:
        envelope = _safe_string(item.get("envelope"))
        label = _safe_string(item.get("label"))
        monthly_amount = _round_amount(float(item.get("monthly_amount") or 0.0))
        if monthly_amount <= 0:
            continue
        candidate_names = [name for name in {envelope, label} if name]
        for candidate in candidate_names:
            key = distribution_name_equivalent_key(candidate) or _slugify(candidate)
            if not key:
                continue
            amounts[key] = _round_amount(max(amounts.get(key, 0.0), monthly_amount))
    for debt in debts:
        envelope_name = _safe_string(debt.get("envelope_name"))
        monthly_payment = _round_amount(float(debt.get("monthly_payment") or 0.0))
        if not envelope_name or monthly_payment <= 0:
            continue
        key = distribution_name_equivalent_key(envelope_name) or _slugify(envelope_name)
        amounts[key] = _round_amount(max(amounts.get(key, 0.0), monthly_payment))
    return amounts


def _build_distribution_eligible_names(
    *,
    selected_envelopes: list[dict[str, Any]],
    known_amounts_by_envelope: dict[str, float],
) -> list[str]:
    known_group_keys = {
        "housing",
        "transport",
        "bills",
        "family",
        "debts",
        "goals",
        "saving",
        "buffer",
        "lifestyle",
        "essentials",
    }
    eligible: list[str] = []
    for item in selected_envelopes:
        final_name = _safe_string(item.get("final_name")) or _safe_string(item.get("name"))
        if not final_name:
            continue
        if is_virtual_parent_envelope_name(final_name):
            # Morona/Flex is a virtual parent, not a real distribution target.
            continue
        explicit_group_key = _safe_string(item.get("group_key"))
        inferred_group_key = _infer_selected_envelope_group(final_name)
        group_key = (
            explicit_group_key
            if explicit_group_key in known_group_keys
            else inferred_group_key
        )
        lock_reason = _safe_string(item.get("lock_reason"))
        if lock_reason in {"fixed_commitment_locked", "guidance_locked"}:
            continue
        custom_amount = _to_number(item.get("custom_amount"))
        final_key = distribution_name_equivalent_key(final_name) or _slugify(final_name)
        base_name = _safe_string(item.get("name"))
        base_key = (
            distribution_name_equivalent_key(base_name) or _slugify(base_name)
            if base_name
            else ""
        )
        known_amount = max(
            custom_amount,
            known_amounts_by_envelope.get(final_key, 0.0),
            known_amounts_by_envelope.get(base_key, 0.0) if base_key else 0.0,
        )
        # Guard against misclassified payloads: if either explicit or inferred group
        # indicates a fixed-commitment envelope with known amount, keep it out of % flex.
        is_commitment_by_explicit = explicit_group_key in COMMITMENT_LOCK_GROUPS
        is_commitment_by_inferred = inferred_group_key in COMMITMENT_LOCK_GROUPS
        if group_key in DISTRIBUTION_STRUCTURAL_GROUP_KEYS:
            continue
        if (is_commitment_by_explicit or is_commitment_by_inferred) and known_amount > 0:
            continue
        eligible.append(final_name)
    return _unique_names(eligible)


def _select_reserve_target_envelope_name(
    *,
    selected_envelopes: list[dict[str, Any]],
) -> str | None:
    buffer_candidates: list[str] = []
    for item in selected_envelopes:
        final_name = _safe_string(item.get("final_name")) or _safe_string(item.get("name"))
        if not final_name or _is_system_envelope_name(final_name):
            continue
        group_key = _safe_string(item.get("group_key")) or _infer_selected_envelope_group(final_name)
        if group_key != "buffer":
            continue
        buffer_candidates.append(final_name)

    if not buffer_candidates:
        return None

    def _buffer_priority(name: str) -> tuple[int, str]:
        normalized = _slugify(name)
        if any(token in normalized for token in ("urgence", "imprevu", "imprevus", "tawari", "emergency")):
            return (0, normalized)
        if "طوار" in name:
            return (0, normalized)
        if "التوازن" in name or "balance" in normalized:
            return (1, normalized)
        return (2, normalized)

    return sorted(buffer_candidates, key=_buffer_priority)[0]


def compute_canonical_apply_state_backend(
    answers: dict[str, Any] | None,
    *,
    existing_state: ExistingApplyState | None = None,
) -> CanonicalApplyState:
    answers = answers if isinstance(answers, dict) else {}
    existing_state = existing_state or ExistingApplyState()
    del existing_state

    sanity = _compute_sanity(answers)
    debts = _build_canonical_debts(answers)
    goals = _build_canonical_goals(answers)
    explicit_selected = _extract_explicit_selected_envelopes(answers)
    selected_envelopes = explicit_selected or _derive_selected_envelopes_legacy(
        answers,
        goals=goals,
        debts=debts,
    )

    categories = _derive_categories(answers)
    fixed_items = _build_fixed_items(answers)
    cycle_normalized_expenses = _build_cycle_normalized_expenses(answers, fixed_items)
    sinking_funds = _build_sinking_funds(cycle_normalized_expenses)
    debt_reality = _build_debt_target_reality_check(answers)
    suggested_debt_extra_per_cycle = _get_suggested_debt_extra_per_cycle(answers)
    planned_debt_per_cycle = _round_amount(max(0.0, _to_number(answers.get("F1_guidance_planned_debt"))))
    planned_goals_per_cycle = _round_amount(max(0.0, _to_number(answers.get("F1_guidance_planned_goals"))))
    planned_flex_per_cycle = _round_amount(max(0.0, _to_number(answers.get("F1_guidance_planned_flex"))))

    debt_posture = _get_priority_profile_debt_posture(answers)
    goal_posture = _get_priority_profile_goal_posture(answers)
    living_margin_level = _get_priority_profile_living_margin_level(answers)
    reserve_policy = _get_priority_profile_reserve_policy(answers)
    reserve_level = _get_priority_profile_reserve_level(answers)
    confidence_label = _get_priority_profile_confidence_label(answers)
    priority_explanation_lines = _get_priority_profile_summary_lines(answers)

    known_amounts_by_envelope = _build_known_amounts_by_envelope(
        cycle_normalized_expenses=cycle_normalized_expenses,
        debts=debts,
    )
    distribution_eligible_names = _build_distribution_eligible_names(
        selected_envelopes=selected_envelopes,
        known_amounts_by_envelope=known_amounts_by_envelope,
    )

    mappings: list[dict[str, str]] = []
    for category in categories:
        fallback = _default_envelope_for_category(category, selected_envelopes)
        resolved = _resolve_selected_envelope_name_for_category(category, selected_envelopes, fallback)
        if resolved:
            mappings.append({"category": category, "envelope": resolved})
    for item in selected_envelopes:
        custom_category = _safe_string(item.get("custom_category"))
        final_name = _safe_string(item.get("final_name")) or _safe_string(item.get("name"))
        if custom_category and final_name:
            canonical_custom = category_key_from_name(custom_category)
            mappings.append({"category": canonical_custom, "envelope": final_name})
            categories.append(canonical_custom)
    categories = _unique_names(categories)
    deduped_mappings: list[dict[str, str]] = []
    seen_mapping_keys: set[tuple[str, str]] = set()
    for mapping in mappings:
        key = (_slugify(mapping["category"]), _slugify(mapping["envelope"]))
        if key in seen_mapping_keys:
            continue
        seen_mapping_keys.add(key)
        deduped_mappings.append(mapping)

    protected_expenses = [
        item for item in cycle_normalized_expenses if item.get("priority_layer") == "protected"
    ]
    planned_future_obligations = [
        item for item in cycle_normalized_expenses if item.get("priority_layer") == "planned_future_obligation"
    ]
    minimum_debt_per_cycle = _round_amount(sum(float(item.get("payment_per_cycle") or 0.0) for item in debts))
    reserve_per_cycle = _round_amount(float(debt_reality.get("reserve_per_cycle") or 0.0))
    income_per_cycle = _to_cycle_amount(float(sanity.get("incomeEstimate") or 0.0), answers)
    protected_expenses_per_cycle = _round_amount(
        sum(float(item.get("per_cycle_amount") or 0.0) for item in protected_expenses)
    )
    planned_obligations_per_cycle = _round_amount(
        sum(float(item.get("per_cycle_amount") or 0.0) for item in planned_future_obligations)
    )

    financial_priority_profile = {
        "recommended_mode": _get_priority_profile_recommended_mode(answers),
        "confidence_label": confidence_label,
        "debt_posture": debt_posture,
        "goal_posture": goal_posture,
        "living_margin_level": living_margin_level,
        "reserve_policy": reserve_policy,
        "reserve_level": reserve_level,
        "explanation_lines": priority_explanation_lines,
    }
    distribution_posture_v1 = {
        "version": "v1",
        "mode": financial_priority_profile["recommended_mode"],
        "living_margin_level": living_margin_level,
        "reserve_level": reserve_level,
        "goal_posture": goal_posture,
        "debt_posture": debt_posture,
        "confidence_label": confidence_label,
        "protected_expenses_policy": "always_fund_first",
        "minimum_debt_policy": "always_keep" if _get_answer_string(answers, "E5_has_debt") == "yes" else "not_applicable",
        "planned_future_obligations_policy": (
            "fund_per_cycle_before_extra_push" if planned_future_obligations else "none_detected"
        ),
        "explanation_lines": priority_explanation_lines,
    }
    reserve_plan_v1 = {
        "policy": reserve_policy,
        "level": reserve_level,
        "starter_seed_monthly": _to_monthly_amount(reserve_per_cycle, answers) if reserve_per_cycle > 0 else 0.0,
        "starter_seed_per_cycle": reserve_per_cycle,
        "target_group": "buffer",
        "target_envelope_name": _select_reserve_target_envelope_name(
            selected_envelopes=selected_envelopes
        ),
        "source": "debt_reality",
    }
    debt_plan_v2 = {
        "strategy": _get_debt_legacy_strategy(answers),
        "focus_debt_index": _get_debt_focus_index(answers) or None,
        "focus_debt_name": debt_reality.get("focus_debt_name"),
        "cycle_label": _get_cycle_label(answers),
        "cadence_label": _get_cadence_label(answers),
        "realistic_capacity_per_cycle": debt_reality.get("realistic_capacity_per_cycle"),
        "realistic_capacity_monthly": debt_reality.get("realistic_capacity_monthly"),
        "reserve_per_cycle": debt_reality.get("reserve_per_cycle"),
        "required_per_cycle": debt_reality.get("required_per_cycle"),
        "required_monthly": debt_reality.get("required_monthly"),
        "suggested_extra_per_cycle": suggested_debt_extra_per_cycle,
        "suggested_extra_monthly": _to_monthly_amount(suggested_debt_extra_per_cycle, answers) if suggested_debt_extra_per_cycle > 0 else 0.0,
        "reserve_ratio": 0.3,
        "status": debt_reality.get("status"),
        "estimated_finish_date": None,
        "planned_extra_per_cycle": planned_debt_per_cycle,
    }
    cash_flow_timing_v1 = {
        "cycle_label": _get_cycle_label(answers),
        "cadence_label": _get_cadence_label(answers),
        "interval_days": infer_sweep_interval_days_from_answers(answers),
        "last_income_date": _get_answer_string(answers, "SWP1_last_income_date") or None,
        "last_income_amount": _to_number(answers.get("SWP2_last_income_amount")) or None,
        "expected_income_per_cycle": income_per_cycle if income_per_cycle > 0 else None,
        "fixed_total_per_cycle": _to_cycle_amount(float(sanity.get("essentialLoadTotal") or 0.0), answers),
        "debt_minimum_total_per_cycle": minimum_debt_per_cycle,
        "protected_expenses_per_cycle": protected_expenses_per_cycle,
        "planned_obligations_per_cycle": planned_obligations_per_cycle,
        "planned_debt_extra_per_cycle": planned_debt_per_cycle,
        "planned_debt_extra_monthly": _to_monthly_amount(planned_debt_per_cycle, answers)
        if planned_debt_per_cycle > 0
        else 0.0,
        "planned_goal_total_per_cycle": planned_goals_per_cycle,
        "planned_goal_total_monthly": _to_monthly_amount(planned_goals_per_cycle, answers)
        if planned_goals_per_cycle > 0
        else 0.0,
        "planned_flex_total_per_cycle": planned_flex_per_cycle,
        "planned_flex_total_monthly": _to_monthly_amount(planned_flex_per_cycle, answers)
        if planned_flex_per_cycle > 0
        else 0.0,
    }
    sinking_fund_policy = {
        "mode": "tracked_future_obligations" if sinking_funds else "none_detected",
        "planned_future_obligations_count": len(sinking_funds),
        "per_cycle_total": _round_amount(sum(float(item.get("contribution_amount") or 0.0) for item in sinking_funds)),
        "labels": [item["name"] for item in sinking_funds],
    }

    return CanonicalApplyState(
        selected_envelopes=selected_envelopes,
        categories=categories,
        mappings=deduped_mappings,
        goals=goals,
        sinking_funds=sinking_funds,
        debts=debts,
        cycle_normalized_expenses_v1=cycle_normalized_expenses,
        reserve_plan_v1=reserve_plan_v1,
        debt_plan_v2=debt_plan_v2,
        financial_priority_profile=financial_priority_profile,
        distribution_posture_v1=distribution_posture_v1,
        sinking_fund_policy=sinking_fund_policy,
        cash_flow_timing_v1=cash_flow_timing_v1,
        sanity_metrics=sanity,
        debt_posture=debt_posture,
        goal_posture=goal_posture,
        living_margin_level=living_margin_level,
        reserve_policy=reserve_policy,
        reserve_level=reserve_level,
        confidence_label=confidence_label,
        priority_explanation_lines=priority_explanation_lines,
        known_amounts_by_envelope=known_amounts_by_envelope,
        distribution_eligible_names=distribution_eligible_names,
    )
