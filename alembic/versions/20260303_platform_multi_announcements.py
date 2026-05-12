"""add multiple announcements support

Revision ID: 20260303_platform_multi_announcements
Revises: 20260302_ip_blocks
Create Date: 2026-03-03 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20260303_platform_multi_announcements"
down_revision = "20260302_ip_blocks"
branch_labels = None
depends_on = None


DEFAULT_PLACEMENTS = [
    "global_sticky",
    "global_popup",
    "global_footer",
    "landing",
    "login",
    "register",
    "app_header",
]


def _normalize_list(value: object, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned if cleaned else fallback


def _to_iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "announcements",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT
                id,
                announcement_enabled,
                announcement_message,
                announcement_type,
                announcement_placements,
                announcement_start_at,
                announcement_end_at,
                announcement_timezone,
                announcement_recurrence,
                announcement_roles,
                announcement_statuses,
                announcement_countries
            FROM platform_settings
            """
        )
    ).mappings()

    platform_settings_table = sa.table(
        "platform_settings",
        sa.column("id", sa.Integer),
        sa.column("announcements", sa.JSON()),
    )

    for row in rows:
        message = (row.get("announcement_message") or "").strip()
        if not message:
            continue

        placements = _normalize_list(
            row.get("announcement_placements"), DEFAULT_PLACEMENTS
        )
        roles = _normalize_list(row.get("announcement_roles"), ["any"])
        statuses = _normalize_list(row.get("announcement_statuses"), ["any"])
        countries = _normalize_list(row.get("announcement_countries"), [])

        announcement_payload = {
            "id": f"legacy-{row['id']}-1",
            "enabled": bool(row.get("announcement_enabled")),
            "message": message,
            "type": (row.get("announcement_type") or "custom").strip() or "custom",
            "placements": placements,
            "start_at": _to_iso(row.get("announcement_start_at")),
            "end_at": _to_iso(row.get("announcement_end_at")),
            "timezone": (row.get("announcement_timezone") or "UTC").strip() or "UTC",
            "recurrence": (row.get("announcement_recurrence") or "none").strip()
            or "none",
            "roles": roles,
            "statuses": statuses,
            "countries": countries,
        }

        conn.execute(
            sa.update(platform_settings_table)
            .where(platform_settings_table.c.id == row["id"])
            .values(announcements=[announcement_payload])
        )

    op.alter_column("platform_settings", "announcements", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "announcements")
