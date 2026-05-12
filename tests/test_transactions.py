from __future__ import annotations

import asyncio
from datetime import date, timedelta
import asyncpg

from fastapi.testclient import TestClient

from tests.utils import register_user


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def fetch_user_anchor_date(database_url: str, user_id: str) -> date:
    async def _fetch() -> date:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            value = await conn.fetchval(
                "SELECT created_at::date FROM users WHERE id = $1",
                user_id,
            )
            return value
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def count_movements_for_transaction(database_url: str, tx_id: str) -> int:
    async def _count() -> int:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM envelope_movements WHERE transaction_id = $1",
                tx_id,
            )
            return int(value or 0)
        finally:
            await conn.close()

    return asyncio.run(_count())


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def create_category(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/categories",
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()


def create_envelope(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/envelopes",
        json={"name": name, "rollover_enabled": False},
    )
    assert response.status_code == 201
    return response.json()


def map_category(
    client: TestClient, user_id: str, category_id: str, envelope_id: str
) -> None:
    response = client.put(
        f"/categories/{category_id}/envelope",
        json={"envelope_id": envelope_id},
    )
    assert response.status_code == 200


def test_expense_with_mapping_creates_movement(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "expense-map@example.com")
    category = create_category(client, user["id"], "Groceries")
    envelope = create_envelope(client, user["id"], "Food")
    map_category(client, user["id"], category["id"], envelope["id"])

    anchor = fetch_user_anchor_date(database_url, user["id"])
    occurred_on = anchor + timedelta(days=1)
    payload = {
        "type": "expense",
        "category_id": category["id"],
        "amount": "42.50",
        "occurred_on": occurred_on.isoformat(),
        "description": "Market",
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["envelope_movement"] is not None
    assert data["envelope_movement"]["amount"] == "-42.50"

    async def _fetch_envelope_id() -> str:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                """
                SELECT envelope_id
                FROM envelope_periods
                WHERE id = $1
                """,
                data["envelope_movement"]["envelope_period_id"],
            )
            return str(row["envelope_id"])
        finally:
            await conn.close()

    period_envelope_id = asyncio.run(_fetch_envelope_id())
    assert period_envelope_id == envelope["id"]

    list_response = client.get("/transactions")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    payload_second = {
        "type": "expense",
        "category_id": category["id"],
        "amount": "5.00",
        "occurred_on": (anchor + timedelta(days=2)).isoformat(),
        "description": "Snack",
    }
    second = client.post("/transactions", json=payload_second)
    assert second.status_code == 201
    assert (
        second.json()["envelope_movement"]["envelope_period_id"]
        == data["envelope_movement"]["envelope_period_id"]
    )


def test_expense_without_mapping_is_rejected(client: TestClient) -> None:
    user = create_user(client, "expense-default@example.com")
    category = create_category(client, user["id"], "Misc")

    payload = {
        "type": "expense",
        "category_id": category["id"],
        "amount": "10.00",
        "occurred_on": date.today().isoformat(),
        "description": None,
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "CATEGORY_NOT_MAPPED"


def test_income_creates_no_movement(client: TestClient) -> None:
    user = create_user(client, "income@example.com")
    category = create_category(client, user["id"], "Salary")

    payload = {
        "type": "income",
        "category_id": category["id"],
        "amount": "500.00",
        "occurred_on": date.today().isoformat(),
        "description": "Pay",
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["envelope_movement"] is not None
    assert data["envelope_movement"]["amount"] == "500.00"


def test_delete_transaction_removes_movement(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "delete-tx@example.com")
    category = create_category(client, user["id"], "Bills")
    envelope = create_envelope(client, user["id"], "Utilities")
    map_category(client, user["id"], category["id"], envelope["id"])

    payload = {
        "type": "expense",
        "category_id": category["id"],
        "amount": "25.00",
        "occurred_on": date.today().isoformat(),
        "description": "Phone",
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201
    tx_id = response.json()["id"]
    assert count_movements_for_transaction(database_url, tx_id) == 1

    delete_response = client.delete(f"/transactions/{tx_id}")
    assert delete_response.status_code == 204
    assert count_movements_for_transaction(database_url, tx_id) == 0

    list_response = client.get("/transactions")
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_delete_income_transaction_updates_dashboard_balances(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "delete-income-balance@example.com")
    category = create_category(client, user["id"], "Salary")

    payload = {
        "type": "income",
        "category_id": category["id"],
        "amount": "500.00",
        "occurred_on": date.today().isoformat(),
        "description": "Pay",
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201
    tx_id = response.json()["id"]
    assert count_movements_for_transaction(database_url, tx_id) == 1

    before = client.get("/dashboard")
    assert before.status_code == 200
    before_data = before.json()
    assert before_data["cash_balance"] in {"500.00", "500", "500.0"}

    delete_response = client.delete(f"/transactions/{tx_id}")
    assert delete_response.status_code == 204
    assert count_movements_for_transaction(database_url, tx_id) == 0

    after = client.get("/dashboard")
    assert after.status_code == 200
    after_data = after.json()
    assert after_data["cash_balance"] in {"0.00", "0", "0.0"}
    assert after_data["net_worth"] in {"0.00", "0", "0.0"}


def test_income_creates_positive_movement_in_cash(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "income-cash@example.com")
    category = create_category(client, user["id"], "Salary")

    payload = {
        "type": "income",
        "category_id": category["id"],
        "amount": "500.00",
        "occurred_on": date.today().isoformat(),
        "description": "Pay",
    }
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["envelope_movement"] is not None
    assert data["envelope_movement"]["amount"] == "500.00"

    envelopes = client.get("/envelopes").json()
    cash = next(e for e in envelopes if e["is_cash"])

    async def _fetch() -> str:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                """
                SELECT envelope_id
                FROM envelope_periods
                WHERE id = $1
                """,
                data["envelope_movement"]["envelope_period_id"],
            )
            return str(row["envelope_id"])
        finally:
            await conn.close()

    period_envelope_id = asyncio.run(_fetch())
    assert period_envelope_id == cash["id"]
