from __future__ import annotations

from datetime import date, timedelta


def period_bounds(
    anchor: date, occurred_on: date, interval_days: int
) -> tuple[date, date]:
    if interval_days <= 0:
        raise ValueError("interval_days must be positive")
    delta_days = (occurred_on - anchor).days
    bucket = delta_days // interval_days
    period_start = anchor + timedelta(days=bucket * interval_days)
    period_end = period_start + timedelta(days=interval_days)
    return period_start, period_end
