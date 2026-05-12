from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_PLATFORM_SETTINGS = {
    "platform_name": "Floussy",
    "support_email": "ELIDRYSSI@GMAIL.COM",
    "registration_enabled": True,
    "advisor_tab_enabled": True,
    "guided_tours_enabled": True,
    "maintenance_mode": False,
    "maintenance_message": "Plateforme en maintenance. Réessayez plus tard.",
    "announcement_enabled": False,
    "announcement_message": "",
    "announcement_type": "custom",
    "maintenance_placements": [
        "global_sticky",
        "global_popup",
        "global_footer",
        "landing",
        "login",
        "register",
        "app_header",
    ],
    "announcement_placements": [
        "global_sticky",
        "global_popup",
        "global_footer",
        "landing",
        "login",
        "register",
        "app_header",
    ],
    "announcement_start_at": None,
    "announcement_end_at": None,
    "announcement_timezone": "UTC",
    "announcement_recurrence": "none",
    "announcement_roles": ["any"],
    "announcement_statuses": ["any"],
    "announcement_countries": [],
    "announcements": [],
    "ai_gateways": [],
    "ai_routing": {
        "default_gateway_id": "",
        "default_model": "",
        "fallback_gateway_ids": [],
        "request_timeout_ms": 60000,
    },
    "rate_limit_login_max": 10,
    "rate_limit_login_window_minutes": 10,
    "rate_limit_register_max": 5,
    "rate_limit_register_window_minutes": 60,
    "rate_limit_api_max": 120,
    "rate_limit_api_window_minutes": 1,
    "default_currency": "MAD",
    "default_sweep_interval_days": 30,
    "password_min_length": 8,
    "default_auto_distribution_enabled": False,
    "account_deletion_grace_days": 30,
}

ALLOWED_ANNOUNCEMENT_TYPES = {
    "security",
    "scheduled_maintenance",
    "product",
    "billing",
    "marketing",
    "legal",
    "performance",
    "custom",
}

ALLOWED_MESSAGE_PLACEMENTS = {
    "global_sticky",
    "global_popup",
    "global_footer",
    "landing",
    "login",
    "register",
    "app_header",
}

ALLOWED_ANNOUNCEMENT_ROLES = {
    "any",
    "public",
    "user",
    "superadmin",
    "admin",
}

ALLOWED_ANNOUNCEMENT_STATUSES = {
    "any",
    "active",
    "limited",
    "suspended",
}

ALLOWED_ANNOUNCEMENT_RECURRENCE = {
    "none",
    "daily",
    "weekly",
    "weekdays",
}


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    platform_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["platform_name"],
    )
    support_email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["support_email"],
    )
    registration_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["registration_enabled"],
    )
    advisor_tab_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["advisor_tab_enabled"],
    )
    guided_tours_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["guided_tours_enabled"],
    )
    maintenance_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["maintenance_mode"],
    )
    maintenance_message: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["maintenance_message"],
    )
    announcement_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_enabled"],
    )
    announcement_message: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_message"],
    )
    announcement_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_type"],
    )
    maintenance_placements: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["maintenance_placements"],
    )
    announcement_placements: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_placements"],
    )
    announcement_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_start_at"],
    )
    announcement_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_end_at"],
    )
    announcement_timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_timezone"],
    )
    announcement_recurrence: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_recurrence"],
    )
    announcement_roles: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_roles"],
    )
    announcement_statuses: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_statuses"],
    )
    announcement_countries: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcement_countries"],
    )
    announcements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["announcements"],
    )
    ai_gateways: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["ai_gateways"],
    )
    ai_routing: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["ai_routing"],
    )
    rate_limit_login_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_login_max"],
    )
    rate_limit_login_window_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_login_window_minutes"],
    )
    rate_limit_register_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_register_max"],
    )
    rate_limit_register_window_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_register_window_minutes"],
    )
    rate_limit_api_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_api_max"],
    )
    rate_limit_api_window_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["rate_limit_api_window_minutes"],
    )
    default_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["default_currency"],
    )
    default_sweep_interval_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["default_sweep_interval_days"],
    )
    password_min_length: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["password_min_length"],
    )
    default_auto_distribution_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["default_auto_distribution_enabled"],
    )
    account_deletion_grace_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_PLATFORM_SETTINGS["account_deletion_grace_days"],
    )
