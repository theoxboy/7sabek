from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi.testclient import TestClient

from tests.utils import register_user


def _sql(database_url: str, query: str, *args) -> None:
    async def _run() -> None:
        url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()

    asyncio.run(_run())


def _promote_superadmin(database_url: str, email: str) -> None:
    _sql(database_url, "UPDATE users SET role = 'superadmin' WHERE email = $1", email)


def _backdate_deletion(database_url: str, email: str, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    _sql(database_url, "UPDATE users SET deleted_at = $1 WHERE email = $2", when, email)


def _user_id(database_url: str, email: str) -> str:
    async def _run() -> str:
        url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(url)
        try:
            row = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        finally:
            await conn.close()
        assert row is not None, f"user {email} not found"
        return str(row["id"])

    return asyncio.run(_run())


def test_purge_is_blocked_until_the_grace_period_has_elapsed(
    client: TestClient, database_url: str
) -> None:
    # Register the target first; register the admin LAST so the live client
    # session belongs to the admin. Promotion happens in-DB and get_current_user
    # re-reads the role on every request (superadmin login itself needs geo /
    # a code, which we sidestep this way - same trick the advisor tests use).
    register_user(client, "purge-target@example.com")
    target_id = _user_id(database_url, "purge-target@example.com")
    register_user(client, "purge-admin@example.com")
    _promote_superadmin(database_url, "purge-admin@example.com")

    # 1. Cannot purge an account that was never soft-deleted.
    r = client.post(f"/users/{target_id}/purge")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "ACCOUNT_NOT_SOFT_DELETED"

    # 2. Soft-delete it, then purging is still blocked during the grace window.
    r = client.delete(f"/users/{target_id}")
    assert r.status_code == 204, r.text
    r = client.post(f"/users/{target_id}/purge")
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("DELETION_GRACE_PERIOD_ACTIVE")

    # 3. Once the 30-day grace has elapsed, the superadmin can force the purge.
    _backdate_deletion(database_url, "purge-target@example.com", days_ago=31)
    r = client.post(f"/users/{target_id}/purge")
    assert r.status_code == 204, r.text

    r = client.get(f"/users/{target_id}?include_deleted=true")
    assert r.status_code == 404


def test_superadmin_can_force_purge_before_the_grace_period_ends(
    client: TestClient, database_url: str
) -> None:
    register_user(client, "force-target@example.com")
    target_id = _user_id(database_url, "force-target@example.com")
    register_user(client, "force-admin@example.com")
    _promote_superadmin(database_url, "force-admin@example.com")

    # Soft-delete, then immediately force-purge while still inside the 30 days.
    assert client.delete(f"/users/{target_id}").status_code == 204
    r = client.post(f"/users/{target_id}/purge")
    assert r.status_code == 409  # normal path still guarded
    r = client.post(f"/users/{target_id}/purge?force=true")
    assert r.status_code == 204, r.text
    assert client.get(f"/users/{target_id}?include_deleted=true").status_code == 404


def test_force_purge_works_on_a_never_soft_deleted_account(
    client: TestClient, database_url: str
) -> None:
    register_user(client, "force-active-target@example.com")
    target_id = _user_id(database_url, "force-active-target@example.com")
    register_user(client, "force-active-admin@example.com")
    _promote_superadmin(database_url, "force-active-admin@example.com")

    assert client.post(f"/users/{target_id}/purge").status_code == 409
    r = client.post(f"/users/{target_id}/purge?force=true")
    assert r.status_code == 204, r.text
    assert client.get(f"/users/{target_id}?include_deleted=true").status_code == 404


def test_force_purge_still_requires_superadmin(
    client: TestClient, database_url: str
) -> None:
    register_user(client, "force-victim@example.com")
    victim_id = _user_id(database_url, "force-victim@example.com")
    register_user(client, "force-plain@example.com")
    r = client.post(f"/users/{victim_id}/purge?force=true")
    assert r.status_code == 403


def test_purge_requires_superadmin(client: TestClient, database_url: str) -> None:
    register_user(client, "purge-victim@example.com")
    victim_id = _user_id(database_url, "purge-victim@example.com")
    _backdate_deletion(database_url, "purge-victim@example.com", days_ago=40)

    # live session is a plain user
    register_user(client, "purge-plain@example.com")
    r = client.post(f"/users/{victim_id}/purge")
    assert r.status_code == 403
