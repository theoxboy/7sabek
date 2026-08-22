from __future__ import annotations

from datetime import date, timedelta


def period_bounds(
    anchor: date, occurred_on: date, interval_days: int
) -> tuple[date, date]:
    if interval_days <= 0:
        raise ValueError("interval_days must be positive")

    if interval_days == 30:
        anchor_day = anchor.day

        def get_month_start_date(m_index: int) -> date:
            y = m_index // 12
            m = (m_index % 12) + 1
            if m == 12:
                next_m_start = date(y + 1, 1, 1)
            else:
                next_m_start = date(y, m + 1, 1)
            last_day = (next_m_start - timedelta(days=1)).day
            d = min(anchor_day, last_day)
            return date(y, m, d)

        init_index = occurred_on.year * 12 + occurred_on.month - 1
        if occurred_on >= get_month_start_date(init_index):
            m_index = init_index
        else:
            m_index = init_index - 1

        return get_month_start_date(m_index), get_month_start_date(m_index + 1)
    else:
        delta_days = (occurred_on - anchor).days
        bucket = delta_days // interval_days
        period_start = anchor + timedelta(days=bucket * interval_days)
        period_end = period_start + timedelta(days=interval_days)
        return period_start, period_end


def get_effective_income_date(
    occurred_on: date, anchor_date: date, sweep_interval_days: int
) -> date:
    normal_start, normal_end = period_bounds(anchor_date, occurred_on, sweep_interval_days)
    buffer_days = min(5, sweep_interval_days // 2)
    if buffer_days > 0 and normal_end - timedelta(days=buffer_days) <= occurred_on < normal_end:
        return normal_end
    return occurred_on


