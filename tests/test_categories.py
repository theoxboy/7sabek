from __future__ import annotations

import asyncio
from typing import Optional
from fastapi.testclient import TestClient
import asyncpg

from tests.utils import register_user


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def create_envelope(client: TestClient, user_id: str, name: str) -> dict:
    payload = {"name": name, "rollover_enabled": False}
    response = client.post("/envelopes", json=payload)
    assert response.status_code == 201
    return response.json()


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def fetch_mapping_count(database_url: str, category_id: str) -> int:
    async def _fetch() -> int:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM category_envelope_map WHERE category_id = $1",
                category_id,
            )
            return int(value)
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def fetch_mapping_envelope(database_url: str, category_id: str) -> Optional[str]:
    async def _fetch() -> Optional[str]:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                "SELECT envelope_id FROM category_envelope_map WHERE category_id = $1",
                category_id,
            )
            return str(row["envelope_id"]) if row else None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_duplicate_category_name_returns_400(client: TestClient) -> None:
    user = create_user(client, "catdup@example.com")

    payload = {"name": "Dining"}
    first = client.post("/categories", json=payload)
    assert first.status_code == 201

    second = client.post("/categories", json=payload)
    assert second.status_code == 400


def test_delete_category_removes_mapping(client: TestClient, database_url: str) -> None:
    user = create_user(client, "catmap@example.com")

    category = client.post("/categories", json={"name": "Fuel"}).json()
    envelope = create_envelope(client, user["id"], "Car")

    map_response = client.put(
        f"/categories/{category['id']}/envelope",
        json={"envelope_id": envelope["id"]},
    )
    assert map_response.status_code == 200
    assert fetch_mapping_count(database_url, category["id"]) == 1

    delete_response = client.delete(f"/categories/{category['id']}")
    assert delete_response.status_code == 204
    assert fetch_mapping_count(database_url, category["id"]) == 0


def test_mapping_upsert_updates_envelope(client: TestClient, database_url: str) -> None:
    user = create_user(client, "mapupdate@example.com")

    category = client.post("/categories", json={"name": "Travel"}).json()
    envelope_a = create_envelope(client, user["id"], "Trips")
    envelope_b = create_envelope(client, user["id"], "Flights")

    first = client.put(
        f"/categories/{category['id']}/envelope",
        json={"envelope_id": envelope_a["id"]},
    )
    assert first.status_code == 200

    second = client.put(
        f"/categories/{category['id']}/envelope",
        json={"envelope_id": envelope_b["id"]},
    )
    assert second.status_code == 200

    assert fetch_mapping_count(database_url, category["id"]) == 1
    assert fetch_mapping_envelope(database_url, category["id"]) == envelope_b["id"]


def test_cannot_map_internal_income_category(client: TestClient) -> None:
    _user = create_user(client, "income-map-forbidden@example.com")

    # Trigger explicit self-heal to ensure internal income category exists.
    heal_response = client.post("/categories/self-heal")
    assert heal_response.status_code == 200

    categories_response = client.get("/categories")
    assert categories_response.status_code == 200
    categories = categories_response.json()
    income_category = next((item for item in categories if item["name"] == "income_general"), None)
    assert income_category is not None

    envelope = create_envelope(client, _user["id"], "Food")
    map_response = client.put(
        f"/categories/{income_category['id']}/envelope",
        json={"envelope_id": envelope["id"]},
    )
    assert map_response.status_code == 400
    assert map_response.json()["detail"] == "CATEGORY_MAPPING_FOR_INTERNAL_INCOME_FORBIDDEN"


def test_self_heal_dry_run_does_not_persist_changes(client: TestClient) -> None:
    _user = create_user(client, "selfheal-dry-run@example.com")
    before = client.get("/categories")
    assert before.status_code == 200
    before_count = len(before.json())

    dry_run_response = client.post("/categories/self-heal?dry_run=true")
    assert dry_run_response.status_code == 200
    payload = dry_run_response.json()
    assert payload["dry_run"] == 1
    assert payload["categories_created"] >= 0

    after = client.get("/categories")
    assert after.status_code == 200
    after_count = len(after.json())
    assert after_count == before_count
