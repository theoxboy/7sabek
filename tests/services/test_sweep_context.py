from __future__ import annotations

from app.services.sweep_context import extract_sweep_bootstrap


def _extract_last_income_amount(raw_amount: str) -> str | None:
    bootstrap = extract_sweep_bootstrap(
        answers={
            "SWP1_last_income_date": "2026-04-26",
            "SWP2_last_income_amount": raw_amount,
        },
        draft_objects={},
    )
    if bootstrap is None:
        return None
    return bootstrap.get("last_income_amount")


def test_extract_sweep_bootstrap_parses_grouped_dot_amount() -> None:
    assert _extract_last_income_amount("8.800") == "8800.00"


def test_extract_sweep_bootstrap_parses_grouped_comma_amount() -> None:
    assert _extract_last_income_amount("8,800") == "8800.00"


def test_extract_sweep_bootstrap_parses_plain_amount() -> None:
    assert _extract_last_income_amount("8800") == "8800.00"

