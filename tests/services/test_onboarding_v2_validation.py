from __future__ import annotations

from app.services.onboarding_v2_validation import (
    build_onboarding_validation_error_detail,
    validate_onboarding_answers,
)


def _base_answers() -> dict[str, str]:
    return {
        "E5_has_debt": "yes",
        "D1_debt_count": "1",
        "D2_debt_name_1": "Credit perso",
        "D3_debt_remaining_amount_1": "25000",
        "D4_debt_native_amount_1": "0",
        "D4_debt_payment_cadence_1": "monthly",
        "D1_debt_has_target_date_1": "no",
    }


def test_validate_onboarding_answers_accepts_zero_current_payment() -> None:
    errors = validate_onboarding_answers(_base_answers())
    assert errors == []


def test_validate_onboarding_answers_rejects_remaining_out_of_range() -> None:
    answers = _base_answers()
    answers["D3_debt_remaining_amount_1"] = "0"
    errors = validate_onboarding_answers(answers)
    assert any(error.code == "DEBT_REMAINING_OUT_OF_RANGE" for error in errors)


def test_validate_onboarding_answers_rejects_target_date_in_past() -> None:
    answers = _base_answers()
    answers["D1_debt_has_target_date_1"] = "yes"
    answers["D5a_debt_target_date_1"] = "2020-01-01"
    errors = validate_onboarding_answers(answers)
    assert any(error.code == "DEBT_TARGET_DATE_INVALID" for error in errors)


def test_build_onboarding_validation_error_detail_includes_code_and_errors() -> None:
    answers = _base_answers()
    answers["D2_debt_name_1"] = "A"
    errors = validate_onboarding_answers(answers)
    detail = build_onboarding_validation_error_detail(errors)
    assert detail["code"] == "ONBOARDING_ANSWERS_INVALID"
    assert isinstance(detail["errors"], list)
    assert len(detail["errors"]) > 0
