from __future__ import annotations

import asyncio

import asyncpg
from fastapi.testclient import TestClient

from tests.utils import register_user


def _sql(database_url: str, query: str, *args):
    async def _run():
        url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


def _superadmin(client: TestClient, database_url: str, email: str) -> None:
    register_user(client, email)
    _sql(database_url, "UPDATE users SET role='superadmin' WHERE email=$1", email)


def test_disabling_guest_mode_blocks_creation(client: TestClient, database_url: str) -> None:
    _superadmin(client, database_url, "sa-gm-off@example.com")
    res = client.patch(
        "/admin/settings",
        json={"guest_mode_enabled": False},
        headers={"x-admin-bypass": "true"},
    )
    assert res.status_code == 200, res.text

    client.cookies.clear()
    blocked = client.post("/auth/guest", json={})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "guest_mode_disabled"

    # platform-status carries the config so the client can render a message
    status = client.get("/public/platform-status").json()
    assert status["guest_mode_enabled"] is False
    assert status["guest_mode_button"] in ("hidden", "message")
    assert "guest_mode_message_fr" in status

    # re-enable so other tests are unaffected
    _sql(database_url, "UPDATE platform_settings SET guest_mode_enabled = true WHERE id = 1")


def test_admin_settings_rejects_a_bad_message_type(client: TestClient, database_url: str) -> None:
    _superadmin(client, database_url, "sa-gm-bad@example.com")
    res = client.patch(
        "/admin/settings",
        json={"guest_mode_message_type": "explosion"},
        headers={"x-admin-bypass": "true"},
    )
    assert res.status_code == 400


def test_guest_list_and_detail_and_purge(client: TestClient, database_url: str) -> None:
    # a guest with one envelope + one expense
    client.cookies.clear()
    guest = client.post("/auth/guest", json={}).json()
    guest_id = guest["user"]["id"]
    client.post("/envelopes", json={"name": "Vacances", "rollover_enabled": True})

    client.cookies.clear()
    _superadmin(client, database_url, "sa-guests@example.com")
    h = {"x-admin-bypass": "true"}

    lst = client.get("/admin/guests?limit=100", headers=h)
    assert lst.status_code == 200, lst.text
    payload = lst.json()
    assert payload["total"] >= 1
    row = next(r for r in payload["rows"] if r["id"] == guest_id)
    assert row["envelope_count"] >= 1
    assert isinstance(row["expense_total"], str)
    assert row["protection_level"] == 40
    assert row["status"] == "active"

    detail = client.get(f"/admin/guests/{guest_id}", headers=h)
    assert detail.status_code == 200, detail.text
    dd = detail.json()
    assert any(e["name"] == "guest_created" for e in dd["events"])
    assert len(dd["envelopes"]) >= 1

    purged = client.delete(f"/admin/guests/{guest_id}", headers=h)
    assert purged.status_code == 204
    assert _sql(database_url, "SELECT 1 FROM users WHERE id=$1", guest_id) == []


def test_guest_list_requires_superadmin(client: TestClient, database_url: str) -> None:
    client.cookies.clear()
    register_user(client, "plain-guest-list@example.com")
    res = client.get("/admin/guests", headers={"x-admin-bypass": "true"})
    assert res.status_code == 403
