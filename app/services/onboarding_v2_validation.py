from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any


DEBT_MAX_COUNT = 3
DEBT_REMAINING_MIN = 1.0
DEBT_REMAINING_MAX = 2_000_000.0
DEBT_PAYMENT_MIN = 0.0
DEBT_PAYMENT_MAX = 100_000.0
DEBT_NAME_MIN_LEN = 2
DEBT_NAME_MAX_LEN = 60
ALLOWED_DEBT_PAYMENT_CADENCES = {"monthly", "weekly", "biweekly", "quarterly", "annual"}


@dataclass(frozen=True)
class OnboardingValidationError:
    code: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


def _safe_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
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
            # Thousands-style dots: 29.998 -> 29998
            if re.match(r"^-?\d{1,3}(\.\d{3})+([.,]\d+)?$", raw):
                cleaned = raw.replace(".", "")
        elif has_comma:
            # Thousands-style commas: 29,998 -> 29998
            if re.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$", raw):
                cleaned = raw.replace(",", "")
            else:
                cleaned = raw.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_iso_date(value: Any) -> date | None:
    text = _safe_string(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _debt_count(answers: dict[str, Any]) -> int:
    explicit_raw = _safe_number(answers.get("D1_debt_count"))
    explicit = int(explicit_raw) if explicit_raw is not None and explicit_raw > 0 else 0
    inferred = 0
    for index in range(1, DEBT_MAX_COUNT + 1):
        if any(
            _safe_string(answers.get(key))
            for key in (
                f"D2_debt_name_{index}",
                f"D3_debt_remaining_amount_{index}",
                f"D4_debt_native_amount_{index}",
                f"D4_debt_monthly_payment_{index}",
                f"D4_debt_payment_cadence_{index}",
                f"D5a_debt_target_date_{index}",
            )
        ):
            inferred = index
    return max(explicit, inferred)


def validate_onboarding_answers(answers: dict[str, Any]) -> list[OnboardingValidationError]:
    if not isinstance(answers, dict):
        return [
            OnboardingValidationError(
                code="ONBOARDING_ANSWERS_INVALID",
                field="answers",
                message="Invalid onboarding answers payload.",
            )
        ]

    errors: list[OnboardingValidationError] = []
    has_debt = _safe_string(answers.get("E5_has_debt")) == "yes"
    if not has_debt:
        return errors

    debt_count = _debt_count(answers)
    if debt_count > DEBT_MAX_COUNT:
        errors.append(
            OnboardingValidationError(
                code="DEBT_COUNT_EXCEEDS_MAX",
                field="D1_debt_count",
                message=f"Debt count cannot exceed {DEBT_MAX_COUNT}.",
            )
        )
        debt_count = DEBT_MAX_COUNT

    if debt_count <= 0:
        errors.append(
            OnboardingValidationError(
                code="DEBT_COUNT_REQUIRED",
                field="D1_debt_count",
                message="At least one debt entry is required when user has debts.",
            )
        )
        return errors

    today = date.today()
    for index in range(1, debt_count + 1):
        name_field = f"D2_debt_name_{index}"
        remaining_field = f"D3_debt_remaining_amount_{index}"
        native_payment_field = f"D4_debt_native_amount_{index}"
        monthly_payment_field = f"D4_debt_monthly_payment_{index}"
        cadence_field = f"D4_debt_payment_cadence_{index}"
        has_target_field = f"D1_debt_has_target_date_{index}"
        target_date_field = f"D5a_debt_target_date_{index}"

        name = _safe_string(answers.get(name_field))
        if len(name) < DEBT_NAME_MIN_LEN or len(name) > DEBT_NAME_MAX_LEN:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_NAME_INVALID",
                    field=name_field,
                    message=f"Debt name must be between {DEBT_NAME_MIN_LEN} and {DEBT_NAME_MAX_LEN} characters.",
                )
            )

        remaining = _safe_number(answers.get(remaining_field))
        if remaining is None:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_REMAINING_REQUIRED",
                    field=remaining_field,
                    message="Debt remaining amount is required.",
                )
            )
        elif remaining < DEBT_REMAINING_MIN or remaining > DEBT_REMAINING_MAX:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_REMAINING_OUT_OF_RANGE",
                    field=remaining_field,
                    message=f"Debt remaining amount must be between {int(DEBT_REMAINING_MIN)} and {int(DEBT_REMAINING_MAX)}.",
                )
            )

        native_payment = _safe_number(answers.get(native_payment_field))
        monthly_payment = _safe_number(answers.get(monthly_payment_field))
        payment = native_payment if native_payment is not None else monthly_payment
        if payment is None:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_PAYMENT_REQUIRED",
                    field=native_payment_field,
                    message="Debt current payment is required.",
                )
            )
        elif payment < DEBT_PAYMENT_MIN or payment > DEBT_PAYMENT_MAX:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_PAYMENT_OUT_OF_RANGE",
                    field=native_payment_field,
                    message=f"Debt payment must be between {int(DEBT_PAYMENT_MIN)} and {int(DEBT_PAYMENT_MAX)}.",
                )
            )

        cadence = _safe_string(answers.get(cadence_field)) or "monthly"
        if cadence not in ALLOWED_DEBT_PAYMENT_CADENCES:
            errors.append(
                OnboardingValidationError(
                    code="DEBT_PAYMENT_CADENCE_INVALID",
                    field=cadence_field,
                    message="Debt payment cadence is invalid.",
                )
            )

        if (
            remaining is not None
            and payment is not None
            and remaining > 0
            and payment > remaining
        ):
            errors.append(
                OnboardingValidationError(
                    code="DEBT_PAYMENT_EXCEEDS_REMAINING",
                    field=native_payment_field,
                    message="Debt payment cannot exceed debt remaining amount.",
                )
            )

        has_target = _safe_string(answers.get(has_target_field)) == "yes"
        if has_target:
            target = _safe_iso_date(answers.get(target_date_field))
            if target is None:
                errors.append(
                    OnboardingValidationError(
                        code="DEBT_TARGET_DATE_REQUIRED",
                        field=target_date_field,
                        message="Debt target date is required.",
                    )
                )
            elif target <= today:
                errors.append(
                    OnboardingValidationError(
                        code="DEBT_TARGET_DATE_INVALID",
                        field=target_date_field,
                        message="Debt target date must be in the future.",
                    )
                )

    return errors


def build_onboarding_validation_error_detail(
    errors: list[OnboardingValidationError],
) -> dict[str, Any]:
    first_message = (
        errors[0].message if errors else "Onboarding answers validation failed."
    )
    return {
        "code": "ONBOARDING_ANSWERS_INVALID",
        "message": first_message,
        "errors": [error.to_dict() for error in errors],
    }
