from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class IncomeReminderBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    frequency: str = Field(pattern="^(monthly|bi_monthly|bi_weekly|weekly|one_off)$")
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    day_of_month_alt: Optional[int] = Field(default=None, ge=1, le=31)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    due_date: Optional[date] = None
    timezone: str = Field(default="UTC", max_length=64)
    is_active: bool = True
    last_declared_on: Optional[date] = None


class IncomeReminderCreate(IncomeReminderBase):
    pass


class IncomeReminderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    frequency: Optional[str] = Field(
        default=None, pattern="^(monthly|bi_monthly|bi_weekly|weekly|one_off)$"
    )
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    day_of_month_alt: Optional[int] = Field(default=None, ge=1, le=31)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    due_date: Optional[date] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    is_active: Optional[bool] = None
    last_declared_on: Optional[date] = None


class IncomeReminderOut(IncomeReminderBase):
    id: UUID
    next_due_on: Optional[date] = None
    last_declared_on: Optional[date] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
