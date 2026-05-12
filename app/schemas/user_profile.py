from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class UserProfileUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    leaderboard_name: Optional[str] = Field(default=None, max_length=40)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    birth_date: Optional[date] = None
    country: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    profile_photo_url: Optional[str] = Field(default=None, max_length=25000000)
