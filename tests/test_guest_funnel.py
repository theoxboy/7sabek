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


def test_guest_funnel_counts_the_journey(client: TestClient, database_url: str) -> None:
    # one guest that activates and claims
    body = client.post("/auth/guest", json={}).json()
    client.post(
        "/envelopes", json={"name": "Vacances", "rollover_enabled": True}
    )
    # give the guest a category + transaction so guest_first_tx fires
    cats = client.get("/categories").json()
    if cats:
        client.post(
            "/transactions",
            json={
                "category_id": cats[0]["id"],
                "type": "expense",
                "amount": 10,
                "occurred_on": "2026-09-03",
            },
        )
    client.post("/auth/guest/ack-recovery")
    client.post(
        "/auth/guest/claim",
        json={"email": "funnel@example.com", "password": "Floussy2026"},
    )

    # a second guest that just looks around
    client.cookies.clear()
    client.post("/auth/guest", json={})

    # a superadmin reads the funnel
    client.cookies.clear()
    register_user(client, "sa-funnel@example.com")
    _sql(database_url, "UPDATE users SET role='superadmin' WHERE email=$1", "sa-funnel@example.com")

    res = client.get("/analytics/guest-funnel?days=30", headers={"x-admin-bypass": "true"})
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["guests_created"] >= 2
    assert d["guests_claimed"] >= 1
    assert d["protection_70"] >= 1
    assert d["claim_by_email"] >= 1
    assert 0.0 <= d["claim_rate"] <= 1.0
    assert isinstance(d["daily"], list)
