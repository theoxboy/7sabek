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


def map_category(
    client: TestClient, user_id: str, category_id: str, envelope_id: str
) -> None:
    response = client.put(
        f"/categories/{category_id}/envelope",
        json={"envelope_id": envelope_id},
    )
    assert response.status_code == 200


def test_dashboard_overview(client: TestClient, database_url: str) -> None:
    user = create_user(client, "dashboard@example.com")

    envelope_a = create_envelope(client, user["id"], "Food")
    envelope_b = create_envelope(client, user["id"], "Bills")

    category_a = create_category(client, user["id"], "Groceries")
    category_b = create_category(client, user["id"], "Utilities")

    map_category(client, user["id"], category_a["id"], envelope_a["id"])
    map_category(client, user["id"], category_b["id"], envelope_b["id"])

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, period_end = period_bounds(
        anchor, date.today(), user["sweep_interval_days"]
    )
    offset_days = 0 if user["sweep_interval_days"] <= 1 else 1
    occurred_on = period_start + timedelta(days=offset_days)

    client.post(
        f"/envelopes/{envelope_a['id']}/allocate",
        json={"amount": "120.00", "occurred_on": occurred_on.isoformat()},
    )
    client.post(
        f"/envelopes/{envelope_b['id']}/allocate",
        json={"amount": "80.00", "occurred_on": occurred_on.isoformat()},
    )

    tx_1 = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_a["id"],
            "amount": "30.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Groceries",
        },
    ).json()
    tx_2 = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_b["id"],
            "amount": "20.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Utilities",
        },
    ).json()
    tx_3 = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_a["id"],
            "amount": "10.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Snack",
        },
    ).json()
    outside_period_tx = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_a["id"],
            "amount": "99.00",
            "occurred_on": period_end.isoformat(),
            "description": "Outside period",
        },
    ).json()

    response = client.get("/dashboard")
    assert response.status_code == 200
    data = response.json()

    assert data["current_period"]["start"]
    assert data["current_period"]["end"]

    # net_worth = sum(closing_balance) for current period
    assert data["net_worth"] == "140.00"
    assert data["period_income"] in {"0.00", "0", "0.0"}
    assert data["period_expenses_mapped"] == "60.00"
    assert data["period_net"] == "-60.00"

    category_totals = {
        item["category_id"]: item["total"] for item in data["spending_by_category"]
    }
    assert category_totals[category_a["id"]] == "40.00"
    assert category_totals[category_b["id"]] == "20.00"

    envelope_totals = {
        item["envelope_id"]: item["total"] for item in data["spending_by_envelope"]
    }
    assert envelope_totals[envelope_a["id"]] == "40.00"
    assert envelope_totals[envelope_b["id"]] == "20.00"

    recent = data["recent_transactions"]
    assert len(recent) == 3
    assert recent[0]["id"] == tx_3["id"]
    assert {item["id"] for item in recent} == {tx_1["id"], tx_2["id"], tx_3["id"]}
    assert outside_period_tx["id"] not in {item["id"] for item in recent}


def test_dashboard_income_does_not_count_as_spending(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "dashboard-income@example.com")

    category = create_category(client, user["id"], "Salary")

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, _period_end = period_bounds(
        anchor, date.today(), user["sweep_interval_days"]
    )
    offset_days = 0 if user["sweep_interval_days"] <= 1 else 1
    occurred_on = period_start + timedelta(days=offset_days)

    response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": category["id"],
            "amount": "500.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Pay",
        },
    )
    assert response.status_code == 201

    response = client.get("/dashboard")
    assert response.status_code == 200
    data = response.json()

    assert data["cash_balance"] in {"500.00", "500", "500.0"}
    assert data["net_worth"] in {"500.00", "500", "500.0"}
    assert data["period_income"] in {"500.00", "500", "500.0"}
    assert data["period_expenses_mapped"] in {"0.00", "0", "0.0"}
    assert data["period_net"] in {"500.00", "500", "500.0"}
    assert data["spending_by_envelope"] == []

    cash_items = [
        item for item in data["envelopes"] if item["envelope"].get("is_cash") is True
    ]
    assert len(cash_items) == 1
    assert cash_items[0]["balance"]["closing_balance"] in {"500.00", "500", "500.0"}


def test_dashboard_spending_only_counts_mapped_expenses(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "dashboard-mapped-only@example.com")

    mapped_envelope = create_envelope(client, user["id"], "Food")
    mapped_category = create_category(client, user["id"], "Groceries")
    unmapped_category = create_category(client, user["id"], "Misc")

    map_category(client, user["id"], mapped_category["id"], mapped_envelope["id"])

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, _period_end = period_bounds(
        anchor, date.today(), user["sweep_interval_days"]
    )
    offset_days = 0 if user["sweep_interval_days"] <= 1 else 1
    occurred_on = period_start + timedelta(days=offset_days)

    client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": mapped_category["id"],
            "amount": "12.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Mapped",
        },
    )
    unmapped_response = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": unmapped_category["id"],
            "amount": "8.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Unmapped",
        },
    )
    assert unmapped_response.status_code == 400
    assert unmapped_response.json()["detail"] == "CATEGORY_NOT_MAPPED"

    response = client.get("/dashboard")
    assert response.status_code == 200
    data = response.json()

    envelope_totals = {
        item["envelope_id"]: item["total"] for item in data["spending_by_envelope"]
    }
    assert envelope_totals == {mapped_envelope["id"]: "12.00"}


def test_dashboard_alerts_ignore_internal_income_category(client: TestClient) -> None:
    _user = create_user(client, "dashboard-alerts-income-ignore@example.com")

    # Create default catalog categories/mappings explicitly.
    heal_response = client.post("/categories/self-heal")
    assert heal_response.status_code == 200

    alerts_response = client.get("/dashboard/alerts")
    assert alerts_response.status_code == 200
    alerts = alerts_response.json()
    assert alerts["unmapped_categories"] == 0


def test_spending_by_envelope_requires_both_dates(client: TestClient) -> None:
    _user = create_user(client, "dashboard-spending-dates@example.com")

    response = client.get("/dashboard/spending-by-envelope", params={"period_start": date.today().isoformat()})
    assert response.status_code == 400
    assert response.json()["detail"] == "start and end are required"


def test_spending_by_envelope_ignores_virtual_parent_envelopes(
    client: TestClient, database_url: str
) -> None:
    user = create_user(client, "dashboard-virtual-envelope@example.com")
    virtual_envelope = create_envelope(client, user["id"], "Flex")
    category = create_category(client, user["id"], "Virtual expense")
    map_category(client, user["id"], category["id"], virtual_envelope["id"])

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, period_end = period_bounds(
        anchor, date.today(), user["sweep_interval_days"]
    )
    offset_days = 0 if user["sweep_interval_days"] <= 1 else 1
    occurred_on = period_start + timedelta(days=offset_days)

    tx_response = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category["id"],
            "amount": "17.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Virtual expense",
        },
    )
    assert tx_response.status_code == 201

    response = client.get(
        "/dashboard/spending-by-envelope",
        params={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json() == []
