from __future__ import annotations

from typing import Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementOut(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    enabled: bool = True
    message: str = Field(max_length=500)
    type: str = Field(default="custom", max_length=40)
    placements: list[str] = Field(default_factory=list)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    timezone: str = Field(default="UTC", max_length=64)
    recurrence: str = Field(default="none", max_length=20)
    roles: list[str] = Field(default_factory=lambda: ["any"])
    statuses: list[str] = Field(default_factory=lambda: ["any"])
    countries: list[str] = Field(default_factory=list)


class AnnouncementPublicOut(AnnouncementOut):
    active: bool


class AIGatewayOut(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="custom", max_length=64)
    protocol: str = Field(default="openai_compatible", max_length=64)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=500)
    auth_header: str = Field(default="Authorization", max_length=120)
    auth_scheme: str = Field(default="Bearer", max_length=40)
    model: str = Field(default="", max_length=160)
    enabled: bool = True
    paths: dict[str, str] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=1000)


class AIRoutingOut(BaseModel):
    default_gateway_id: str = Field(default="", max_length=64)
    default_model: str = Field(default="", max_length=160)
    fallback_gateway_ids: list[str] = Field(default_factory=list)
    request_timeout_ms: int = Field(default=60000, ge=1000, le=600000)


class PlatformSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    platform_name: str
    support_email: str
    registration_enabled: bool
    advisor_tab_enabled: bool
    guided_tours_enabled: bool
    maintenance_mode: bool
    maintenance_message: str
    announcement_enabled: bool
    announcement_message: str
    announcement_type: str
    maintenance_placements: list[str]
    announcement_placements: list[str]
    announcement_start_at: Optional[datetime] = None
    announcement_end_at: Optional[datetime] = None
    announcement_timezone: str
    announcement_recurrence: str
    announcement_roles: list[str]
    announcement_statuses: list[str]
    announcement_countries: list[str]
    announcements: list[AnnouncementOut] = Field(default_factory=list)
    ai_gateways: list[AIGatewayOut] = Field(default_factory=list)
    ai_routing: AIRoutingOut = Field(default_factory=AIRoutingOut)
    rate_limit_login_max: int
    rate_limit_login_window_minutes: int
    rate_limit_register_max: int
    rate_limit_register_window_minutes: int
    rate_limit_api_max: int
    rate_limit_api_window_minutes: int
    default_currency: str
    default_sweep_interval_days: int
    password_min_length: int
    default_auto_distribution_enabled: bool
    account_deletion_grace_days: int


class PlatformSettingsUpdate(BaseModel):
    platform_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    support_email: Optional[str] = Field(default=None, min_length=5, max_length=200)
    registration_enabled: Optional[bool] = None
    advisor_tab_enabled: Optional[bool] = None
    guided_tours_enabled: Optional[bool] = None
    maintenance_mode: Optional[bool] = None
    maintenance_message: Optional[str] = Field(default=None, max_length=500)
    announcement_enabled: Optional[bool] = None
    announcement_message: Optional[str] = Field(default=None, max_length=500)
    announcement_type: Optional[str] = Field(default=None, max_length=40)
    maintenance_placements: Optional[list[str]] = None
    announcement_placements: Optional[list[str]] = None
    announcement_start_at: Optional[datetime] = None
    announcement_end_at: Optional[datetime] = None
    announcement_timezone: Optional[str] = Field(default=None, max_length=64)
    announcement_recurrence: Optional[str] = Field(default=None, max_length=20)
    announcement_roles: Optional[list[str]] = None
    announcement_statuses: Optional[list[str]] = None
    announcement_countries: Optional[list[str]] = None
    announcements: Optional[list[AnnouncementOut]] = None
    ai_gateways: Optional[list[AIGatewayOut]] = None
    ai_routing: Optional[AIRoutingOut] = None
    rate_limit_login_max: Optional[int] = Field(default=None, ge=0, le=1000)
    rate_limit_login_window_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    rate_limit_register_max: Optional[int] = Field(default=None, ge=0, le=1000)
    rate_limit_register_window_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    rate_limit_api_max: Optional[int] = Field(default=None, ge=0, le=100000)
    rate_limit_api_window_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    default_sweep_interval_days: Optional[int] = Field(default=None, ge=1, le=365)
    password_min_length: Optional[int] = Field(default=None, ge=6, le=128)
    default_auto_distribution_enabled: Optional[bool] = None
    account_deletion_grace_days: Optional[int] = Field(default=None, ge=1, le=365)


class PlatformStatusOut(BaseModel):
    platform_name: str
    support_email: str
    guided_tours_enabled: bool
    maintenance_mode: bool
    advisor_tab_enabled: bool
    maintenance_message: str
    announcement_enabled: bool
    announcement_message: str
    announcement_type: str
    maintenance_placements: list[str]
    announcement_placements: list[str]
    announcement_active: bool
    announcement_start_at: Optional[datetime] = None
    announcement_end_at: Optional[datetime] = None
    announcement_timezone: str
    announcement_recurrence: str
    announcement_roles: list[str]
    announcement_statuses: list[str]
    announcement_countries: list[str]
    announcements: list[AnnouncementPublicOut] = Field(default_factory=list)
    account_deletion_grace_days: int
