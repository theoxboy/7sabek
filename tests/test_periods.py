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


def test_period_bounds_monthly_align_mid_month() -> None:
    anchor = date(2026, 6, 25)

    # 1. Before anchor in same month
    start, end = period_bounds(anchor, date(2026, 6, 9), 30)
    assert start == date(2026, 5, 25)
    assert end == date(2026, 6, 25)

    # 2. At anchor date
    start, end = period_bounds(anchor, date(2026, 6, 25), 30)
    assert start == date(2026, 6, 25)
    assert end == date(2026, 7, 25)

    # 3. After anchor date
    start, end = period_bounds(anchor, date(2026, 7, 10), 30)
    assert start == date(2026, 6, 25)
    assert end == date(2026, 7, 25)

    # 4. At next boundary
    start, end = period_bounds(anchor, date(2026, 7, 25), 30)
    assert start == date(2026, 7, 25)
    assert end == date(2026, 8, 25)


def test_period_bounds_monthly_align_month_end() -> None:
    anchor = date(2026, 1, 31)

    # 1. In February (non-leap year, capped to 28)
    start, end = period_bounds(anchor, date(2026, 2, 15), 30)
    assert start == date(2026, 1, 31)
    assert end == date(2026, 2, 28)

    # 2. Occurred on the capped date
    start, end = period_bounds(anchor, date(2026, 2, 28), 30)
    assert start == date(2026, 2, 28)
    assert end == date(2026, 3, 31)


def test_period_bounds_monthly_align_leap_year() -> None:
    anchor = date(2024, 1, 31)  # 2024 is a leap year

    # 1. In February (capped to 29)
    start, end = period_bounds(anchor, date(2024, 2, 15), 30)
    assert start == date(2024, 1, 31)
    assert end == date(2024, 2, 29)

    # 2. Occurred on the leap day
    start, end = period_bounds(anchor, date(2024, 2, 29), 30)
    assert start == date(2024, 2, 29)
    assert end == date(2024, 3, 31)

