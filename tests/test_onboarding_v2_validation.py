from __future__ import annotations

from app.services.onboarding_v2_validation import validate_onboarding_answers


def test_negative_salaried_income_rejected() -> None:
    errors = validate_onboarding_answers(
        {"Q0_income_type": "salaried", "S2a_salary_amount": "-500"}
    )
    codes = [error.code for error in errors]
    assert "INCOME_AMOUNT_OUT_OF_RANGE" in codes
    error = next(e for e in errors if e.code == "INCOME_AMOUNT_OUT_OF_RANGE")
    assert error.field == "S2a_salary_amount"


def test_income_above_max_rejected() -> None:
    errors = validate_onboarding_answers(
        {"Q0_income_type": "freelancer", "F7_min_income": "3000000"}
    )
    codes = [error.code for error in errors]
    assert "INCOME_AMOUNT_OUT_OF_RANGE" in codes


def test_missing_income_amount_is_not_an_error() -> None:
    errors = validate_onboarding_answers({"Q0_income_type": "salaried"})
    assert errors == []


def test_valid_income_amount_is_not_an_error() -> None:
    for income_type, field in (
        ("salaried", "S2a_salary_amount"),
        ("hirafi", "H3_income_profile_min"),
        ("freelancer", "F7_min_income"),
        ("mixed", "M3_min_income"),
    ):
        errors = validate_onboarding_answers(
            {"Q0_income_type": income_type, field: "5000"}
        )
        assert errors == [], f"unexpected errors for {income_type}: {errors}"


def test_unknown_income_type_is_not_validated() -> None:
    errors = validate_onboarding_answers(
        {"Q0_income_type": "unknown_type", "some_field": "-999"}
    )
    assert errors == []
