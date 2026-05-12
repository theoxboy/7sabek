from __future__ import annotations

import asyncio
from datetime import date, timedelta

import asyncpg
from fastapi.testclient import TestClient

from tests.utils import register_user

from app.services.periods import period_bounds


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


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def create_envelope(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/envelopes",
        json={"name": name, "rollover_enabled": False},
    )
    assert response.status_code == 201
    return response.json()


def create_category(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/categories",
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()


def fetch_cash_period_movement_amount(
    database_url: str, period_id: str, amount: str
) -> bool:
    async def _fetch() -> bool:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM envelope_movements
                WHERE envelope_period_id = $1 AND amount = $2
                """,
                period_id,
                amount,
            )
            return row is not None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_allocate_from_cash_happy_path(client: TestClient, database_url: str) -> None:
    user = create_user(client, "cash-alloc@example.com")

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, _ = period_bounds(
        anchor, anchor + timedelta(days=1), user["sweep_interval_days"]
    )
    occurred_on = period_start + timedelta(days=1)

    category = create_category(client, user["id"], "Salary")
    income_response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": category["id"],
            "amount": "500.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Pay",
        },
    )
    assert income_response.status_code == 201

    target = create_envelope(client, user["id"], "Food")

    response = client.post(
        f"/envelopes/{target['id']}/allocate-from-cash",
        json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
    )
    assert response.status_code == 201
    data = response.json()

    assert data["allocation"]["amount"] == "100.00"
    balance_response = client.get(
        f"/envelopes/{target['id']}/periods/{data['allocation']['envelope_period_id']}/balance",
    )
    assert balance_response.status_code == 200
    assert balance_response.json()["closing_balance"] == "100.00"

    cash_period_id = income_response.json()["envelope_movement"]["envelope_period_id"]
    assert fetch_cash_period_movement_amount(database_url, cash_period_id, "-100.00")


def test_allocate_from_cash_insufficient_funds(client: TestClient) -> None:
    user = create_user(client, "cash-low@example.com")
    target = create_envelope(client, user["id"], "Food")

    response = client.post(
        f"/envelopes/{target['id']}/allocate-from-cash",
        json={"amount": "10.00", "occurred_on": date.today().isoformat()},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient cash balance"


def test_allocate_from_cash_forbidden_on_cash(client: TestClient) -> None:
    user = create_user(client, "cash-forbidden@example.com")

    envelopes = client.get("/envelopes").json()
    cash = next(e for e in envelopes if e["is_cash"])

    response = client.post(
        f"/envelopes/{cash['id']}/allocate-from-cash",
        json={"amount": "10.00", "occurred_on": date.today().isoformat()},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Cash allocation is not allowed"
