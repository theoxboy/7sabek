from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import asyncpg

from tests.utils import register_user


def _extract_token(link: str) -> str:
    parsed = urlparse(link)
    values = parse_qs(parsed.query).get("token", [])
    assert values
    return values[0]


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _mark_superadmin(database_url: str, email: str, first_name: str = "OMAR") -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            await conn.execute(
                """
                UPDATE users
                SET role='superadmin',
                    first_name=$2
                WHERE lower(email)=lower($1)
                """,
                email,
                first_name,
            )
        finally:
            await conn.close()

    asyncio.run(_run())


def _set_password_reset_block(
    database_url: str,
    email: str,
    *,
    mode: str,
    until: datetime | None = None,
    reason: str | None = None,
) -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            await conn.execute(
                """
                UPDATE users
                SET password_reset_block_mode=$2,
                    password_reset_blocked_until=$3,
                    password_reset_block_reason=$4,
                    password_reset_blocked_at=now()
                WHERE lower(email)=lower($1)
                """,
                email,
                mode,
                until,
                reason,
            )
        finally:
            await conn.close()

    asyncio.run(_run())


def test_password_reset_request_always_returns_ok(client) -> None:
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "unknown-user@example.com", "locale": "fr"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_password_reset_confirm_updates_password(client, monkeypatch) -> None:
    email = "reset-flow@example.com"
    old_password = "Floussy2026"
    new_password = "Floussy2027"
    register_user(client, email=email, password=old_password)

    captured: dict[str, str] = {}

    async def fake_mailer(*, to_email: str, reset_link: str, locale: str = "fr") -> None:
        captured["to_email"] = to_email
        captured["reset_link"] = reset_link
        captured["locale"] = locale

    monkeypatch.setattr("app.api.routes.auth.send_password_reset_email", fake_mailer)

    request_response = client.post(
        "/auth/password-reset/request",
        json={"email": email, "locale": "fr"},
    )
    assert request_response.status_code == 200
    assert captured["to_email"] == email

    token = _extract_token(captured["reset_link"])

    confirm_response = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "ok"

    old_login = client.post("/auth/login", json={"email": email, "password": old_password})
    assert old_login.status_code == 401

    new_login = client.post("/auth/login", json={"email": email, "password": new_password})
    assert new_login.status_code == 200

    reused = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "Another2028"},
    )
    assert reused.status_code == 400


def test_password_reset_confirm_rejects_same_password(client, monkeypatch) -> None:
    email = "reset-same@example.com"
    password = "Floussy2026"
    register_user(client, email=email, password=password)

    captured: dict[str, str] = {}

    async def fake_mailer(*, to_email: str, reset_link: str, locale: str = "fr") -> None:
        captured["to_email"] = to_email
        captured["reset_link"] = reset_link
        captured["locale"] = locale

    monkeypatch.setattr("app.api.routes.auth.send_password_reset_email", fake_mailer)

    request_response = client.post(
        "/auth/password-reset/request",
        json={"email": email, "locale": "fr"},
    )
    assert request_response.status_code == 200
    token = _extract_token(captured["reset_link"])

    confirm_response = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": password},
    )
    assert confirm_response.status_code == 400
    assert "different from the current password" in confirm_response.json().get("detail", "")


def test_superadmin_password_reset_requires_code_and_first_name(
    client, monkeypatch, database_url: str
) -> None:
    email = "superadmin-reset@example.com"
    old_password = "Floussy2026"
    new_password = "Floussy2027"
    register_user(client, email=email, password=old_password)
    _mark_superadmin(database_url, email, first_name="OMAR")

    captured: dict[str, str] = {}

    async def fake_mailer(*, to_email: str, reset_link: str, locale: str = "fr") -> None:
        captured["to_email"] = to_email
        captured["reset_link"] = reset_link
        captured["locale"] = locale

    monkeypatch.setattr("app.api.routes.auth.send_password_reset_email", fake_mailer)

    request_response = client.post(
        "/auth/password-reset/request",
        json={"email": email, "locale": "fr"},
    )
    assert request_response.status_code == 200
    token = _extract_token(captured["reset_link"])

    missing_verification = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )
    assert missing_verification.status_code == 400
    assert "Superadmin verification failed" in missing_verification.json().get("detail", "")

    wrong_verification = client.post(
        "/auth/password-reset/confirm",
        json={
            "token": token,
            "new_password": new_password,
            "superadmin_code": "1234",
            "superadmin_first_name": "OMAR",
        },
    )
    assert wrong_verification.status_code == 400
    assert "Superadmin verification failed" in wrong_verification.json().get("detail", "")

    valid_verification = client.post(
        "/auth/password-reset/confirm",
        json={
            "token": token,
            "new_password": new_password,
            "superadmin_code": "4303",
            "superadmin_first_name": "OMAR",
        },
    )
    assert valid_verification.status_code == 200
    assert valid_verification.json()["status"] == "ok"


def test_password_reset_request_rejected_when_user_is_blocked(client, database_url: str) -> None:
    email = "blocked-reset@example.com"
    password = "Floussy2026"
    register_user(client, email=email, password=password)
    _set_password_reset_block(
        database_url,
        email,
        mode="temporary",
        until=datetime.now(timezone.utc) + timedelta(hours=12),
        reason="Too many suspicious attempts",
    )

    response = client.post(
        "/auth/password-reset/request",
        json={"email": email, "locale": "fr"},
    )
    assert response.status_code == 403
    assert "temporairement bloquée" in response.json().get("detail", "")
