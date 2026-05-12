from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import EmailStr


class UserCreate(BaseModel):
    email: str
    currency: str = Field(min_length=3, max_length=3)
    sweep_interval_days: int = Field(gt=0)
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    leaderboard_name: Optional[str] = Field(default=None, max_length=40)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    birth_date: Optional[date] = None
    country: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    profile_photo_url: Optional[str] = Field(default=None, max_length=25000000)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    status: str
    must_reset_password: bool
    is_beta_tester: bool
    force_onboarding_v2_review: bool
    force_tour_replay_version: int = 0
    has_completed_onboarding_v2: bool = False
    currency: str
    sweep_interval_days: int
    next_sweep_date: date
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    leaderboard_name: Optional[str] = None
    phone_number: Optional[str] = None
    birth_date: Optional[date] = None
    country: Optional[str] = None
    city: Optional[str] = None
    profile_photo_url: Optional[str] = None
    deleted_at: Optional[datetime] = None
    suspended_until: Optional[datetime] = None
    password_reset_requests_total: int = 0
    password_reset_last_requested_at: Optional[datetime] = None
    password_reset_blocked: bool = False
    password_reset_block_mode: str = "none"
    password_reset_blocked_until: Optional[datetime] = None
    password_reset_block_reason: Optional[str] = None
    password_reset_blocked_at: Optional[datetime] = None


class UserDataSummaryOut(BaseModel):
    transactions: int
    categories: int
    envelopes: int


class AdminSummaryOut(BaseModel):
    users: int
    categories: int
    envelopes: int
    transactions: int


class TopClientOut(BaseModel):
    user_id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    income_total: float


class AdminUserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    sweep_interval_days: Optional[int] = Field(default=None, gt=0)
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    leaderboard_name: Optional[str] = Field(default=None, max_length=40)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    birth_date: Optional[date] = None
    country: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    profile_photo_url: Optional[str] = Field(default=None, max_length=25000000)
    status: Optional[str] = None
    must_reset_password: Optional[bool] = None
    is_beta_tester: Optional[bool] = None
    force_onboarding_v2_review: Optional[bool] = None
    force_tour_replay_version: Optional[int] = Field(default=None, ge=0)


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=8)


class PasswordResetBlockUpdateIn(BaseModel):
    mode: str = Field(pattern="^(none|temporary|permanent)$")
    duration_value: Optional[int] = Field(default=None, ge=1, le=120)
    duration_unit: Optional[str] = Field(default=None, pattern="^(hours|days|months)$")
    reason: Optional[str] = Field(default=None, max_length=255)


class PasswordResetBlockOut(BaseModel):
    status: str
    user_id: UUID
    blocked: bool
    mode: str
    blocked_until: Optional[datetime] = None
    reason: Optional[str] = None
    blocked_at: Optional[datetime] = None


class UserSessionHistoryOut(BaseModel):
    id: UUID
    status: str
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    browser: Optional[str] = None
    os: Optional[str] = None
    device: Optional[str] = None
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_accuracy_m: Optional[float] = None
    geo_label: Optional[str] = None
    ip_blocked: bool = False
    created_at: datetime
    last_seen_at: datetime
    ended_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class UserSessionHistoryListOut(BaseModel):
    user_id: UUID
    user_email: str
    sessions: list[UserSessionHistoryOut]


class UserSessionActionIn(BaseModel):
    action: str = Field(pattern="^(end|revoke)$")


class UserSessionActionOut(BaseModel):
    status: str
    action: str
    user_id: UUID
    session: UserSessionHistoryOut
    should_logout: bool = False


class UserSessionBlockIPIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=255)


class UserSessionBlockIPOut(BaseModel):
    status: str
    blocked_ip: str
    already_blocked: bool
    affected_active_sessions: int
    user_id: UUID
    session: UserSessionHistoryOut
    should_logout: bool = False


class BlockedIPOut(BaseModel):
    id: UUID
    ip_address: str
    reason: Optional[str] = None
    created_at: datetime
    blocked_by_user_id: Optional[UUID] = None
    blocked_by_email: Optional[str] = None
    source_session_id: Optional[UUID] = None
    source_user_id: Optional[UUID] = None
    source_user_email: Optional[str] = None


class BlockedIPListOut(BaseModel):
    items: list[BlockedIPOut]


class UnblockIPOut(BaseModel):
    status: str
    id: UUID
    ip_address: str
