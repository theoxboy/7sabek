from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import IncomeReminder, User
from app.schemas.income_reminder import (
    IncomeReminderCreate,
    IncomeReminderOut,
    IncomeReminderUpdate,
)

router = APIRouter(prefix="/income-reminders")


def _today_in_tz(tz_name: Optional[str]) -> date:
    """Calendar date "now" in the reminder's timezone.

    A reminder near midnight local time must not roll to the wrong day just
    because the server clock is on UTC.
    """
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date()


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _next_monthly_date(base_date: date, day_of_month: int) -> date:
    day = max(1, min(day_of_month, 31))
    year = base_date.year
    month = base_date.month
    last_day = _last_day_of_month(year, month)
    candidate = date(year, month, min(day, last_day))
    if candidate >= base_date:
        return candidate
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    last_day = _last_day_of_month(year, month)
    return date(year, month, min(day, last_day))


def _next_bi_monthly_date(
    base_date: date, day_of_month: int, day_of_month_alt: int
) -> date:
    days = sorted({max(1, min(day_of_month, 31)), max(1, min(day_of_month_alt, 31))})
    year = base_date.year
    month = base_date.month
    last_day = _last_day_of_month(year, month)
    candidates = [
        date(year, month, min(day, last_day))
        for day in days
    ]
    candidates = sorted(set(candidates))
    for candidate in candidates:
        if candidate >= base_date:
            return candidate
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    last_day = _last_day_of_month(year, month)
    return date(year, month, min(days[0], last_day))


def _next_weekly_date(base_date: date, day_of_week: int) -> date:
    day = max(0, min(day_of_week, 6))
    delta = (day - base_date.weekday()) % 7
    return base_date + timedelta(days=delta)


def _compute_next_due(
    base_date: date,
    frequency: str,
    day_of_month: Optional[int],
    day_of_month_alt: Optional[int],
    day_of_week: Optional[int],
    due_date: Optional[date],
    last_declared_on: Optional[date],
    previous_due: Optional[date] = None,
) -> Optional[date]:
    if frequency == "one_off":
        return due_date
    if frequency == "weekly":
        # Re-anchor to the scheduled weekday so declaring a day late does not
        # drift the cadence permanently. Fall back to +7d only when no weekday
        # is on the reminder.
        if day_of_week is not None:
            anchor = last_declared_on or base_date
            return _next_weekly_date(anchor + timedelta(days=1), day_of_week)
        if last_declared_on is not None:
            return last_declared_on + timedelta(days=7)
        raise ValueError("day_of_week required")
    if frequency == "bi_weekly":
        if last_declared_on is None:
            raise ValueError("last_declared_on required")
        # Keep the 15-day grid anchored to the original schedule; roll it
        # forward past the declaration date rather than restarting from the
        # click, so a late declaration does not shift every future date.
        if previous_due is not None:
            candidate = previous_due + timedelta(days=15)
            while candidate <= last_declared_on:
                candidate += timedelta(days=15)
            return candidate
        return last_declared_on + timedelta(days=15)
    if frequency == "monthly":
        if day_of_month is None:
            raise ValueError("day_of_month required")
        return _next_monthly_date(base_date + timedelta(days=1), day_of_month)
    if frequency == "bi_monthly":
        if day_of_month is None or day_of_month_alt is None:
            raise ValueError("day_of_month and day_of_month_alt required")
        return _next_bi_monthly_date(
            base_date + timedelta(days=1), day_of_month, day_of_month_alt
        )
    raise ValueError("invalid frequency")


def _validate_payload(payload: IncomeReminderCreate | IncomeReminderUpdate) -> None:
    frequency = payload.frequency
    if frequency is None:
        return
    if frequency == "monthly":
        if payload.day_of_month is None and payload.last_declared_on is None:
            raise HTTPException(status_code=400, detail="day_of_month required")
    elif frequency == "bi_monthly":
        if payload.day_of_month is None or payload.day_of_month_alt is None:
            raise HTTPException(
                status_code=400, detail="day_of_month and day_of_month_alt required"
            )
    elif frequency == "weekly":
        if payload.day_of_week is None and payload.last_declared_on is None:
            raise HTTPException(status_code=400, detail="day_of_week required")
    elif frequency == "bi_weekly":
        if payload.last_declared_on is None:
            raise HTTPException(status_code=400, detail="last_declared_on required")
    elif frequency == "one_off":
        if payload.due_date is None:
            raise HTTPException(status_code=400, detail="due_date required")


def _to_out(reminder: IncomeReminder) -> IncomeReminderOut:
    return IncomeReminderOut(
        id=reminder.id,
        name=reminder.name,
        frequency=reminder.frequency,
        day_of_month=reminder.day_of_month,
        day_of_month_alt=reminder.day_of_month_alt,
        day_of_week=reminder.day_of_week,
        due_date=reminder.due_date,
        timezone=reminder.timezone,
        next_due_on=reminder.next_due_on,
        last_declared_on=reminder.last_declared_on,
        is_active=reminder.is_active,
        created_at=reminder.created_at,
        updated_at=reminder.updated_at,
    )


@router.get("", response_model=List[IncomeReminderOut])
async def list_income_reminders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[IncomeReminderOut]:
    result = await db.execute(
        select(IncomeReminder)
        .where(IncomeReminder.user_id == current_user.id)
        .order_by(IncomeReminder.created_at.desc())
    )
    return [_to_out(reminder) for reminder in result.scalars().all()]


@router.post("", response_model=IncomeReminderOut, status_code=status.HTTP_201_CREATED)
async def create_income_reminder(
    payload: IncomeReminderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncomeReminderOut:
    _validate_payload(payload)
    base_date = payload.last_declared_on or _today_in_tz(payload.timezone)
    if payload.frequency == "monthly" and payload.day_of_month is None:
        payload.day_of_month = base_date.day
    if payload.frequency == "weekly" and payload.day_of_week is None:
        payload.day_of_week = base_date.weekday()
    next_due_on = _compute_next_due(
        base_date,
        payload.frequency,
        payload.day_of_month,
        payload.day_of_month_alt,
        payload.day_of_week,
        payload.due_date,
        payload.last_declared_on,
    )
    reminder = IncomeReminder(
        user_id=current_user.id,
        name=payload.name,
        frequency=payload.frequency,
        day_of_month=payload.day_of_month,
        day_of_month_alt=payload.day_of_month_alt,
        day_of_week=payload.day_of_week,
        due_date=payload.due_date,
        timezone=payload.timezone,
        next_due_on=next_due_on,
        last_declared_on=payload.last_declared_on,
        is_active=payload.is_active,
    )
    db.add(reminder)
    await db.commit()
    await db.refresh(reminder)
    return _to_out(reminder)


@router.patch("/{reminder_id}", response_model=IncomeReminderOut)
async def update_income_reminder(
    reminder_id: UUID,
    payload: IncomeReminderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncomeReminderOut:
    result = await db.execute(
        select(IncomeReminder).where(
            IncomeReminder.user_id == current_user.id,
            IncomeReminder.id == reminder_id,
        )
    )
    reminder = result.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(status_code=404, detail="reminder not found")

    _validate_payload(payload)

    fields_sent = payload.model_fields_set
    frequency_changed = (
        payload.frequency is not None and payload.frequency != reminder.frequency
    )

    if payload.name is not None:
        reminder.name = payload.name
    if payload.frequency is not None:
        reminder.frequency = payload.frequency

    # Only overwrite a schedule field when the client actually sent it. When the
    # frequency genuinely changes, fields that no longer apply are additionally
    # reset - but a PATCH that merely repeats the current frequency must not
    # silently wipe day_of_month / day_of_week and re-derive them from "today".
    if "day_of_month" in fields_sent or frequency_changed:
        reminder.day_of_month = payload.day_of_month
    if "day_of_month_alt" in fields_sent or frequency_changed:
        reminder.day_of_month_alt = payload.day_of_month_alt
    if "day_of_week" in fields_sent or frequency_changed:
        reminder.day_of_week = payload.day_of_week
    if "due_date" in fields_sent or frequency_changed:
        reminder.due_date = payload.due_date
    if payload.timezone is not None:
        reminder.timezone = payload.timezone
    if payload.is_active is not None:
        reminder.is_active = payload.is_active
    if payload.last_declared_on is not None:
        reminder.last_declared_on = payload.last_declared_on

    base_date = reminder.last_declared_on or _today_in_tz(reminder.timezone)
    if reminder.frequency == "monthly" and reminder.day_of_month is None:
        reminder.day_of_month = base_date.day
    if reminder.frequency == "weekly" and reminder.day_of_week is None:
        reminder.day_of_week = base_date.weekday()

    if reminder.is_active:
        next_due_on = _compute_next_due(
            base_date,
            reminder.frequency,
            reminder.day_of_month,
            reminder.day_of_month_alt,
            reminder.day_of_week,
            reminder.due_date,
            reminder.last_declared_on,
        )
        reminder.next_due_on = next_due_on
    else:
        reminder.next_due_on = None

    await db.commit()
    await db.refresh(reminder)
    return _to_out(reminder)


@router.post("/{reminder_id}/mark-declared", response_model=IncomeReminderOut)
async def mark_income_declared(
    reminder_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncomeReminderOut:
    result = await db.execute(
        select(IncomeReminder).where(
            IncomeReminder.user_id == current_user.id,
            IncomeReminder.id == reminder_id,
        )
    )
    reminder = result.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(status_code=404, detail="reminder not found")

    today = _today_in_tz(reminder.timezone)
    reminder.last_declared_on = today

    if reminder.frequency == "one_off":
        reminder.is_active = False
        reminder.next_due_on = None
    else:
        next_due_on = _compute_next_due(
            today,
            reminder.frequency,
            reminder.day_of_month,
            reminder.day_of_month_alt,
            reminder.day_of_week,
            reminder.due_date,
            reminder.last_declared_on,
            previous_due=reminder.next_due_on,
        )
        reminder.next_due_on = next_due_on

    await db.commit()
    await db.refresh(reminder)
    return _to_out(reminder)


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_income_reminder(
    reminder_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(IncomeReminder).where(
            IncomeReminder.user_id == current_user.id,
            IncomeReminder.id == reminder_id,
        )
    )
    reminder = result.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    await db.delete(reminder)
    await db.commit()
    return None
