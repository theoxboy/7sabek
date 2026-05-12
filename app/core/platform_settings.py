from __future__ import annotations

from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_settings import (
    DEFAULT_PLATFORM_SETTINGS,
    PlatformSettings,
)


async def get_platform_settings(
    db: AsyncSession, create_if_missing: bool = False
) -> PlatformSettings:
    settings = await db.get(PlatformSettings, 1)
    if settings is not None:
        return settings
    if not create_if_missing:
        return PlatformSettings(id=1, **DEFAULT_PLATFORM_SETTINGS)
    settings = PlatformSettings(id=1, **DEFAULT_PLATFORM_SETTINGS)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings


def build_blocked_message(support_email: str) -> str:
    return (
        "Votre compte a été limité ou suspendu. "
        f"Contactez {support_email} pour plus d’informations."
    )


def _safe_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _normalize_string_list(values: Any, fallback: list[str]) -> list[str]:
    if not isinstance(values, list):
        return fallback
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return cleaned if cleaned else fallback


def _coerce_datetime(value: Any, timezone_name: str | None) -> datetime | None:
    if value is None:
        return None
    tz = _safe_timezone(timezone_name)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed
    return None


def _normalize_announcement_item(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    timezone_name = str(raw.get("timezone") or "UTC").strip() or "UTC"
    message = str(raw.get("message") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not label:
        label = f"Annonce #{index}"
    return {
        "id": str(raw.get("id") or f"announcement-{index}").strip()
        or f"announcement-{index}",
        "label": label,
        "enabled": bool(raw.get("enabled", True)),
        "message": message,
        "type": str(raw.get("type") or "custom").strip() or "custom",
        "placements": _normalize_string_list(
            raw.get("placements"),
            list(DEFAULT_PLATFORM_SETTINGS["announcement_placements"]),
        ),
        "start_at": _coerce_datetime(raw.get("start_at"), timezone_name),
        "end_at": _coerce_datetime(raw.get("end_at"), timezone_name),
        "timezone": timezone_name,
        "recurrence": str(raw.get("recurrence") or "none").strip() or "none",
        "roles": _normalize_string_list(raw.get("roles"), ["any"]),
        "statuses": _normalize_string_list(raw.get("statuses"), ["any"]),
        "countries": _normalize_string_list(raw.get("countries"), []),
    }


def _build_legacy_announcement_item(settings: PlatformSettings) -> dict[str, Any] | None:
    message = (settings.announcement_message or "").strip()
    if not message:
        return None
    return {
        "id": "legacy-primary",
        "label": "Annonce #1",
        "enabled": bool(settings.announcement_enabled),
        "message": message,
        "type": settings.announcement_type or "custom",
        "placements": _normalize_string_list(
            settings.announcement_placements,
            list(DEFAULT_PLATFORM_SETTINGS["announcement_placements"]),
        ),
        "start_at": settings.announcement_start_at,
        "end_at": settings.announcement_end_at,
        "timezone": settings.announcement_timezone or "UTC",
        "recurrence": settings.announcement_recurrence or "none",
        "roles": _normalize_string_list(settings.announcement_roles, ["any"]),
        "statuses": _normalize_string_list(settings.announcement_statuses, ["any"]),
        "countries": _normalize_string_list(settings.announcement_countries, []),
    }


def get_normalized_announcements(settings: PlatformSettings) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    raw_items = settings.announcements if isinstance(settings.announcements, list) else []
    for index, item in enumerate(raw_items, start=1):
        normalized = _normalize_announcement_item(item, index)
        if normalized["message"]:
            items.append(normalized)
    if items:
        return items
    legacy = _build_legacy_announcement_item(settings)
    return [legacy] if legacy else []


def is_announcement_item_active(item: dict[str, Any]) -> bool:
    if not item.get("enabled"):
        return False
    message = str(item.get("message") or "").strip()
    if not message:
        return False

    tz = _safe_timezone(item.get("timezone"))
    now = datetime.now(tz)

    start_at = _coerce_datetime(item.get("start_at"), item.get("timezone"))
    end_at = _coerce_datetime(item.get("end_at"), item.get("timezone"))

    recurrence = str(item.get("recurrence") or "none").lower().strip()
    if recurrence == "none":
        if start_at is not None:
            start_at = start_at.astimezone(tz)
            if now < start_at:
                return False
        if end_at is not None:
            end_at = end_at.astimezone(tz)
            if now > end_at:
                return False
        return True

    if start_at is not None:
        start_at = start_at.astimezone(tz)
        if now.date() < start_at.date():
            return False
    if end_at is not None:
        end_at = end_at.astimezone(tz)
        if now.date() > end_at.date():
            return False

    if recurrence == "daily":
        return True
    if recurrence == "weekdays":
        return now.weekday() < 5
    if recurrence == "weekly":
        if start_at is None:
            return True
        return now.weekday() == start_at.weekday()

    return True


def is_announcement_active(settings: PlatformSettings) -> bool:
    if not settings.announcement_enabled:
        return False
    return any(is_announcement_item_active(item) for item in get_normalized_announcements(settings))


def get_public_announcements(settings: PlatformSettings) -> list[dict[str, Any]]:
    global_enabled = bool(settings.announcement_enabled)
    items = get_normalized_announcements(settings)
    result: list[dict[str, Any]] = []
    for item in items:
        payload = dict(item)
        payload["active"] = global_enabled and is_announcement_item_active(item)
        result.append(payload)
    return result


def build_maintenance_message(message: str | None) -> str:
    if message and message.strip():
        return message.strip()
    return "Plateforme en maintenance. Réessayez plus tard."
