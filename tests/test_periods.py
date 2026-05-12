from __future__ import annotations

from datetime import date

from app.services.periods import period_bounds


def test_period_bounds_before_anchor() -> None:
    anchor = date(2026, 1, 10)
    start, end = period_bounds(anchor, date(2026, 1, 9), 10)
    assert start == date(2025, 12, 31)
    assert end == anchor


def test_period_bounds_at_anchor() -> None:
    anchor = date(2026, 1, 10)
    start, end = period_bounds(anchor, anchor, 10)
    assert start == anchor
    assert end == date(2026, 1, 20)
