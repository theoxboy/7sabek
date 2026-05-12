from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GamificationSummaryOut(BaseModel):
    points_total: int
    points_weekly: int
    points_monthly: int
    current_streak_days: int
    longest_streak_days: int
    freeze_tokens: int
    freeze_pending: bool
    freeze_pending_date: Optional[date] = None
    level: int
    level_label: str
    level_progress: int
    next_level_points: int
    leaderboard_opt_in: bool
    display_name: str
    week_start: date
    month_start: date


class GamificationSettingsUpdate(BaseModel):
    leaderboard_opt_in: bool = Field(default=False)


class GamificationLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    points: int
    scope: str
    occurred_on: date
    created_at: datetime


class LeaderboardEntryOut(BaseModel):
    rank: int
    display_name: str
    points: int


class LeaderboardOut(BaseModel):
    period: str
    entries: list[LeaderboardEntryOut]
    user_rank: Optional[int] = None
    user_points: Optional[int] = None
    opt_in: bool
