from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    currency: str = Field(min_length=3, max_length=3)
    sweep_interval_days: int = Field(gt=0)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    phone_number: str = Field(min_length=6, max_length=30)
    birth_date: date
    country: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    profile_photo_url: Optional[str] = Field(default=None, max_length=25000000)
    mfa_consent: bool = False
    defer_onboarding_v2: bool = False
    onboarding_v2_answers: Optional[dict[str, Any]] = None
    onboarding_v2_draft_objects: Optional[dict[str, Any]] = None
    recaptcha_token: Optional[str] = Field(default=None, min_length=1, max_length=4096)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_accuracy_m: Optional[float] = Field(default=None, ge=0)
    geo_label: Optional[str] = Field(default=None, max_length=255)
    browser: Optional[str] = Field(default=None, max_length=120)
    os: Optional[str] = Field(default=None, max_length=120)
    device: Optional[str] = Field(default=None, max_length=80)


class AuthOut(BaseModel):
    id: str
    email: EmailStr
    role: str
    status: str
    must_reset_password: bool
    is_beta_tester: bool
    force_onboarding_v2_review: bool
    force_tour_replay_version: int = 0
    currency: str
    sweep_interval_days: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    leaderboard_name: Optional[str] = None
    phone_number: Optional[str] = None
    birth_date: Optional[date] = None
    country: Optional[str] = None
    city: Optional[str] = None
    profile_photo_url: Optional[str] = None


class WebLoginTokenOut(BaseModel):
    token: str
    expires_at: datetime


class WebLoginExchangeIn(BaseModel):
    token: str
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_accuracy_m: Optional[float] = Field(default=None, ge=0)
    geo_label: Optional[str] = Field(default=None, max_length=255)
    browser: Optional[str] = Field(default=None, max_length=120)
    os: Optional[str] = Field(default=None, max_length=120)
    device: Optional[str] = Field(default=None, max_length=80)


class ForcePasswordResetIn(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str = Field(min_length=8)


class PasswordResetRequestIn(BaseModel):
    email: EmailStr
    locale: Optional[str] = Field(default="fr", max_length=10)


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=16, max_length=1024)
    new_password: str = Field(min_length=8)
    superadmin_code: Optional[str] = Field(default=None, min_length=4, max_length=16)
    superadmin_first_name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class PasswordResetTokenInfoIn(BaseModel):
    token: str = Field(min_length=16, max_length=1024)


class PasswordResetTokenInfoOut(BaseModel):
    status: str
    valid: bool
    requires_superadmin_verification: bool


class StatusOut(BaseModel):
    status: str
    message: Optional[str] = None


class SuperadminSessionOut(BaseModel):
    id: UUID
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    browser: Optional[str] = None
    os: Optional[str] = None
    device: Optional[str] = None
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_accuracy_m: Optional[float] = None
    geo_label: Optional[str] = None
    created_at: datetime
    last_seen_at: datetime


class SuperadminSessionHistoryOut(SuperadminSessionOut):
    status: str
    ended_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class SuperadminSessionStateOut(BaseModel):
    current_session_id: UUID
    has_conflict: bool
    sessions: list[SuperadminSessionOut]


class SuperadminSessionHistoryListOut(BaseModel):
    sessions: list[SuperadminSessionHistoryOut]


class ResolveSuperadminSessionIn(BaseModel):
    keep_session_id: UUID


class ResolveSuperadminSessionOut(BaseModel):
    status: str
    kept_session_id: UUID
    should_logout: bool
