from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.routes import income_reminders as ir
from app.api.routes.income_reminders import _compute_next_due, _today_in_tz
from tests.utils import register_user


def test_today_in_tz_uses_the_reminder_timezone_across_midnight() -> None:
    # 2025-12-31 23:30 UTC is already 2026-01-01 in Casablanca (UTC+1 in winter).
    fixed = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc)
    fake_datetime = MagicMock(wraps=datetime)
    fake_datetime.now.side_effect = lambda tz=None: fixed.astimezone(tz)

    with patch.object(ir, "datetime", fake_datetime):
        assert _today_in_tz("Africa/Casablanca") == date(2026, 1, 1)
        assert _today_in_tz("UTC") == date(2025, 12, 31)


def test_today_in_tz_falls_back_to_utc_on_bad_zone() -> None:
    assert isinstance(_today_in_tz("not/a/zone"), date)
    assert isinstance(_today_in_tz(None), date)


def test_weekly_reanchors_to_scheduled_weekday_when_declared_late() -> None:
    # Reminder scheduled for Fridays (weekday 4). Declared 2 days late, on Sunday.
    declared_late = date(2026, 3, 1)  # Sunday
    next_due = _compute_next_due(
        base_date=declared_late,
        frequency="weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=4,
        due_date=None,
        last_declared_on=declared_late,
    )
    # Snaps back to the next Friday, not declared_late + 7 (which would be a Sunday).
    assert next_due == date(2026, 3, 6)
    assert next_due.weekday() == 4


def test_weekly_on_time_declaration_is_seven_days_later() -> None:
    on_time = date(2026, 3, 6)  # Friday
    next_due = _compute_next_due(
        base_date=on_time,
        frequency="weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=4,
        due_date=None,
        last_declared_on=on_time,
    )
    assert next_due == date(2026, 3, 13)


def test_weekly_without_weekday_falls_back_to_plus_seven() -> None:
    declared = date(2026, 3, 1)
    next_due = _compute_next_due(
        base_date=declared,
        frequency="weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=None,
        due_date=None,
        last_declared_on=declared,
    )
    assert next_due == date(2026, 3, 8)


def test_bi_weekly_keeps_grid_anchored_to_previous_due() -> None:
    previous_due = date(2026, 3, 2)
    declared_late = date(2026, 3, 5)  # 3 days late
    next_due = _compute_next_due(
        base_date=declared_late,
        frequency="bi_weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=None,
        due_date=None,
        last_declared_on=declared_late,
        previous_due=previous_due,
    )
    # 15-day grid from the original schedule, not declared_late + 15.
    assert next_due == date(2026, 3, 17)


def test_bi_weekly_rolls_forward_when_declaration_is_very_late() -> None:
    previous_due = date(2026, 3, 2)
    declared_very_late = date(2026, 3, 20)  # more than one 15-day step late
    next_due = _compute_next_due(
        base_date=declared_very_late,
        frequency="bi_weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=None,
        due_date=None,
        last_declared_on=declared_very_late,
        previous_due=previous_due,
    )
    # 2026-03-17 <= declared, so roll to the next grid point.
    assert next_due == date(2026, 4, 1)
    assert next_due > declared_very_late


def test_bi_weekly_without_previous_due_falls_back_to_plus_fifteen() -> None:
    declared = date(2026, 3, 5)
    next_due = _compute_next_due(
        base_date=declared,
        frequency="bi_weekly",
        day_of_month=None,
        day_of_month_alt=None,
        day_of_week=None,
        due_date=None,
        last_declared_on=declared,
    )
    assert next_due == date(2026, 3, 20)


def test_monthly_is_unaffected_and_stays_on_day_of_month() -> None:
    declared_late = date(2026, 3, 3)
    next_due = _compute_next_due(
        base_date=declared_late,
        frequency="monthly",
        day_of_month=1,
        day_of_month_alt=None,
        day_of_week=None,
        due_date=None,
        last_declared_on=declared_late,
    )
    assert next_due == date(2026, 4, 1)


def test_patch_frequency_only_repeat_does_not_wipe_schedule_fields(
    client: TestClient,
) -> None:
    register_user(client, "reminder-patch@example.com")

    created = client.post(
        "/income-reminders",
        json={"name": "Salaire", "frequency": "weekly", "day_of_week": 4},
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    assert created.json()["day_of_week"] == 4

    # PATCH that repeats the current frequency and supplies last_declared_on on
    # a *different* weekday must not silently move the reminder to that weekday.
    patched = client.patch(
        f"/income-reminders/{reminder_id}",
        json={"frequency": "weekly", "last_declared_on": "2026-03-01"},  # a Sunday
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["day_of_week"] == 4  # still Friday, not Sunday (6)


def test_patch_changing_frequency_resets_now_irrelevant_fields(
    client: TestClient,
) -> None:
    register_user(client, "reminder-patch-freq@example.com")

    created = client.post(
        "/income-reminders",
        json={"name": "Salaire", "frequency": "monthly", "day_of_month": 3},
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    patched = client.patch(
        f"/income-reminders/{reminder_id}",
        json={"frequency": "weekly", "day_of_week": 1},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["frequency"] == "weekly"
    assert body["day_of_week"] == 1
    assert body["day_of_month"] is None  # monthly's day no longer applies


def test_patch_unrelated_field_keeps_schedule(client: TestClient) -> None:
    register_user(client, "reminder-patch-name@example.com")

    created = client.post(
        "/income-reminders",
        json={
            "name": "Salaire",
            "frequency": "bi_monthly",
            "day_of_month": 1,
            "day_of_month_alt": 15,
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    patched = client.patch(
        f"/income-reminders/{reminder_id}", json={"name": "Salaire principal"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["day_of_month"] == 1
    assert body["day_of_month_alt"] == 15
