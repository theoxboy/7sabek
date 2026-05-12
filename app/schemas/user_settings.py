from __future__ import annotations

from typing import Optional
from datetime import date

from pydantic import BaseModel, Field


class UserSettingsOut(BaseModel):
    currency: str
    sweep_interval_days: int
    auto_distribution_enabled: bool = False
    auto_sweep_enabled: bool = True
    next_sweep_date: Optional[date] = None


class UserSettingsUpdate(BaseModel):
    sweep_interval_days: Optional[int] = Field(default=None, ge=1, le=365)
    currency: Optional[str] = None
    auto_distribution_enabled: Optional[bool] = None
    auto_sweep_enabled: Optional[bool] = None
    next_sweep_date: Optional[date] = None
