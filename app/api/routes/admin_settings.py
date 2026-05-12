from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.admin_activity import create_admin_log
from app.core.platform_settings import get_normalized_announcements, get_platform_settings
from app.core.rate_limit import get_client_ip
from app.db.session import get_db
from app.models import User
from app.models.platform_settings import (
    ALLOWED_ANNOUNCEMENT_RECURRENCE,
    ALLOWED_ANNOUNCEMENT_ROLES,
    ALLOWED_ANNOUNCEMENT_STATUSES,
    ALLOWED_ANNOUNCEMENT_TYPES,
    ALLOWED_MESSAGE_PLACEMENTS,
)
from app.schemas.platform_settings import PlatformSettingsOut, PlatformSettingsUpdate

router = APIRouter(prefix="/admin/settings")


def _safe_timezone(value: str | None) -> str:
    timezone_name = (value or "UTC").strip() or "UTC"
    try:
        ZoneInfo(timezone_name)
    except Exception:
        return "UTC"
    return timezone_name


def _parse_datetime(
    value: str | datetime | None,
    timezone_name: str,
) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid datetime format",
            ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(_safe_timezone(timezone_name)))
    return parsed


def _validate_placements(placements: list[str], detail: str) -> None:
    invalid = [value for value in placements if value not in ALLOWED_MESSAGE_PLACEMENTS]
    if invalid:
        raise HTTPException(status_code=400, detail=detail)


def _validate_roles(roles: list[str]) -> None:
    invalid = [value for value in roles if value not in ALLOWED_ANNOUNCEMENT_ROLES]
    if invalid:
        raise HTTPException(status_code=400, detail="Invalid announcement roles")


def _validate_statuses(statuses: list[str]) -> None:
    invalid = [value for value in statuses if value not in ALLOWED_ANNOUNCEMENT_STATUSES]
    if invalid:
        raise HTTPException(status_code=400, detail="Invalid announcement statuses")


def _normalize_announcements(payload_announcements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    ids_seen: set[str] = set()

    for index, item in enumerate(payload_announcements, start=1):
        item_id = str(item.get("id") or f"announcement-{index}").strip()
        if not item_id:
            item_id = f"announcement-{index}"
        if item_id in ids_seen:
            raise HTTPException(status_code=400, detail="Duplicate announcement id")
        ids_seen.add(item_id)

        message = str(item.get("message") or "").strip()
        if not message:
            if bool(item.get("enabled", True)):
                raise HTTPException(
                    status_code=400,
                    detail="Announcement message is required for enabled announcements",
                )
            continue
        label = str(item.get("label") or "").strip() or f"Annonce #{index}"

        announcement_type = str(item.get("type") or "custom").strip() or "custom"
        if announcement_type not in ALLOWED_ANNOUNCEMENT_TYPES:
            raise HTTPException(status_code=400, detail="Invalid announcement type")

        recurrence = str(item.get("recurrence") or "none").strip() or "none"
        if recurrence not in ALLOWED_ANNOUNCEMENT_RECURRENCE:
            raise HTTPException(
                status_code=400,
                detail="Invalid announcement recurrence",
            )

        timezone_name = _safe_timezone(item.get("timezone"))
        placements = item.get("placements") or []
        if not isinstance(placements, list):
            placements = []
        _validate_placements(placements, "Invalid announcement placement")

        roles = item.get("roles") or ["any"]
        if not isinstance(roles, list):
            roles = ["any"]
        _validate_roles(roles)

        statuses = item.get("statuses") or ["any"]
        if not isinstance(statuses, list):
            statuses = ["any"]
        _validate_statuses(statuses)

        countries = item.get("countries") or []
        if not isinstance(countries, list):
            countries = []

        start_at = _parse_datetime(item.get("start_at"), timezone_name)
        end_at = _parse_datetime(item.get("end_at"), timezone_name)

        normalized.append(
            {
                "id": item_id,
                "label": label[:120],
                "enabled": bool(item.get("enabled", True)),
                "message": message,
                "type": announcement_type,
                "placements": [str(value) for value in placements],
                "start_at": start_at,
                "end_at": end_at,
                "timezone": timezone_name,
                "recurrence": recurrence,
                "roles": [str(value) for value in roles],
                "statuses": [str(value) for value in statuses],
                "countries": [str(value) for value in countries],
            }
        )

    return normalized


def _serialize_announcements_for_storage(
    announcements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in announcements:
        start_at = item.get("start_at")
        end_at = item.get("end_at")
        serialized.append(
            {
                **item,
                "start_at": start_at.isoformat() if isinstance(start_at, datetime) else None,
                "end_at": end_at.isoformat() if isinstance(end_at, datetime) else None,
            }
        )
    return serialized


def _build_response(settings) -> PlatformSettingsOut:
    response = PlatformSettingsOut.model_validate(settings)
    response.announcements = get_normalized_announcements(settings)
    return response


def _normalize_ai_gateways(payload_gateways: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    ids_seen: set[str] = set()

    for index, item in enumerate(payload_gateways, start=1):
        gateway_id = str(item.get("id") or f"gateway-{index}").strip()[:64]
        if not gateway_id:
            gateway_id = f"gateway-{index}"
        if gateway_id in ids_seen:
            raise HTTPException(status_code=400, detail="Duplicate AI gateway id")
        ids_seen.add(gateway_id)

        name = str(item.get("name") or f"Gateway #{index}").strip()[:120]
        provider = str(item.get("provider") or "custom").strip()[:64]
        protocol = str(item.get("protocol") or "openai_compatible").strip()[:64]
        base_url = str(item.get("base_url") or "").strip()[:500]
        if not base_url:
            raise HTTPException(status_code=400, detail="AI gateway base_url is required")

        auth_header = str(item.get("auth_header") or "Authorization").strip()[:120]
        auth_scheme = str(item.get("auth_scheme") or "Bearer").strip()[:40]
        model = str(item.get("model") or "").strip()[:160]
        api_key = str(item.get("api_key") or "").strip()[:500]
        enabled = bool(item.get("enabled", True))
        notes = str(item.get("notes") or "").strip()[:1000]

        raw_paths = item.get("paths") if isinstance(item.get("paths"), dict) else {}
        paths = {
            str(k)[:80]: str(v)[:200]
            for k, v in raw_paths.items()
            if str(k).strip() and str(v).strip()
        }

        raw_headers = item.get("extra_headers") if isinstance(item.get("extra_headers"), dict) else {}
        extra_headers = {
            str(k)[:120]: str(v)[:500]
            for k, v in raw_headers.items()
            if str(k).strip()
        }

        normalized.append(
            {
                "id": gateway_id,
                "name": name,
                "provider": provider,
                "protocol": protocol,
                "base_url": base_url,
                "api_key": api_key,
                "auth_header": auth_header,
                "auth_scheme": auth_scheme,
                "model": model,
                "enabled": enabled,
                "paths": paths,
                "extra_headers": extra_headers,
                "notes": notes,
            }
        )

    return normalized


def _normalize_ai_routing(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload or {}
    fallback_raw = data.get("fallback_gateway_ids")
    fallback_ids = fallback_raw if isinstance(fallback_raw, list) else []
    fallback_ids = [str(item).strip()[:64] for item in fallback_ids if str(item).strip()]
    timeout_ms = data.get("request_timeout_ms")
    try:
        timeout_value = int(timeout_ms if timeout_ms is not None else 60000)
    except (TypeError, ValueError):
        timeout_value = 60000
    timeout_value = max(1000, min(timeout_value, 600000))

    return {
        "default_gateway_id": str(data.get("default_gateway_id") or "").strip()[:64],
        "default_model": str(data.get("default_model") or "").strip()[:160],
        "fallback_gateway_ids": fallback_ids,
        "request_timeout_ms": timeout_value,
    }


@router.get("", response_model=PlatformSettingsOut)
async def get_platform_settings_route(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlatformSettingsOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    settings = await get_platform_settings(db, create_if_missing=False)
    return _build_response(settings)


@router.patch("", response_model=PlatformSettingsOut)
async def update_platform_settings_route(
    payload: PlatformSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlatformSettingsOut:
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")

    settings = await get_platform_settings(db, create_if_missing=True)
    update_data = payload.model_dump(exclude_unset=True)

    timezone_value = _safe_timezone(
        update_data.get("announcement_timezone") or settings.announcement_timezone
    )

    if "announcement_type" in update_data:
        announcement_type = update_data["announcement_type"]
        if announcement_type not in ALLOWED_ANNOUNCEMENT_TYPES:
            raise HTTPException(status_code=400, detail="Invalid announcement type")

    if "announcement_recurrence" in update_data:
        recurrence = update_data["announcement_recurrence"]
        if recurrence not in ALLOWED_ANNOUNCEMENT_RECURRENCE:
            raise HTTPException(
                status_code=400,
                detail="Invalid announcement recurrence",
            )

    if "announcement_roles" in update_data:
        _validate_roles(update_data["announcement_roles"] or [])

    if "announcement_statuses" in update_data:
        _validate_statuses(update_data["announcement_statuses"] or [])

    if "maintenance_placements" in update_data:
        _validate_placements(
            update_data["maintenance_placements"] or [],
            "Invalid maintenance placement",
        )

    if "announcement_placements" in update_data:
        _validate_placements(
            update_data["announcement_placements"] or [],
            "Invalid announcement placement",
        )

    if "announcement_start_at" in update_data:
        update_data["announcement_start_at"] = _parse_datetime(
            update_data.get("announcement_start_at"),
            timezone_value,
        )

    if "announcement_end_at" in update_data:
        update_data["announcement_end_at"] = _parse_datetime(
            update_data.get("announcement_end_at"),
            timezone_value,
        )

    if "announcements" in update_data:
        normalized_announcements = _normalize_announcements(update_data["announcements"] or [])
        update_data["announcements"] = _serialize_announcements_for_storage(
            normalized_announcements
        )

        if normalized_announcements:
            first = normalized_announcements[0]
            update_data.setdefault(
                "announcement_enabled",
                any(item.get("enabled") for item in normalized_announcements),
            )
            update_data["announcement_message"] = first["message"]
            update_data["announcement_type"] = first["type"]
            update_data["announcement_placements"] = first["placements"]
            update_data["announcement_start_at"] = first["start_at"]
            update_data["announcement_end_at"] = first["end_at"]
            update_data["announcement_timezone"] = first["timezone"]
            update_data["announcement_recurrence"] = first["recurrence"]
            update_data["announcement_roles"] = first["roles"]
            update_data["announcement_statuses"] = first["statuses"]
            update_data["announcement_countries"] = first["countries"]
        else:
            update_data["announcement_message"] = ""
            update_data["announcement_type"] = "custom"
            update_data["announcement_start_at"] = None
            update_data["announcement_end_at"] = None
            update_data["announcement_timezone"] = "UTC"
            update_data["announcement_recurrence"] = "none"
            update_data["announcement_roles"] = ["any"]
            update_data["announcement_statuses"] = ["any"]
            update_data["announcement_countries"] = []

    if "ai_gateways" in update_data:
        update_data["ai_gateways"] = _normalize_ai_gateways(update_data["ai_gateways"] or [])

    if "ai_routing" in update_data:
        update_data["ai_routing"] = _normalize_ai_routing(update_data["ai_routing"] or {})

    for key, value in update_data.items():
        setattr(settings, key, value)

    await db.commit()
    await db.refresh(settings)

    if update_data:
        updated_keys = ", ".join(sorted(update_data.keys()))
        truncated_keys = (
            f"{updated_keys[:340]}…"
            if len(updated_keys) > 340
            else updated_keys
        )
        await create_admin_log(
            db,
            event_type="settings_updated",
            status="success",
            message=f"Paramètres mis à jour: {truncated_keys}",
            actor_email=current_user.email,
            actor_ip=get_client_ip(request),
        )

    return _build_response(settings)
