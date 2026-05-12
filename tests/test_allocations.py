from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.utils import register_user


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def create_envelope(
    client: TestClient, user_id: str, name: str, rollover_enabled: bool
) -> dict:
    response = client.post(
        "/envelopes",
        json={"name": name, "rollover_enabled": rollover_enabled},
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


def test_allocate_increases_balance(client: TestClient) -> None:
    user = create_user(client, "alloc@example.com")
    envelope = create_envelope(client, user["id"], "Buffer", False)

    occurred_on = date(2026, 1, 1)
    payload = {"amount": "100.00", "occurred_on": occurred_on.isoformat()}
    response = client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json=payload,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["balance"]["opening_balance"] == "0"
    assert data["balance"]["total_allocations"] == "100.00"
    assert data["balance"]["total_spent"] == "0"
    assert data["balance"]["closing_balance"] == "100.00"

    period_id = data["allocation"]["envelope_period_id"]
    balance_response = client.get(
        f"/envelopes/{envelope['id']}/periods/{period_id}/balance",
    )
    assert balance_response.status_code == 200
    assert balance_response.json()["closing_balance"] == "100.00"

    second_payload = {
        "amount": "20.00",
        "occurred_on": (occurred_on + timedelta(days=1)).isoformat(),
    }
    second = client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json=second_payload,
    )
    assert second.status_code == 201
    assert second.json()["allocation"]["envelope_period_id"] == period_id


def test_expense_decreases_balance(client: TestClient) -> None:
    user = create_user(client, "alloc-expense@example.com")
    envelope = create_envelope(client, user["id"], "Food", False)
    category = create_category(client, user["id"], "Groceries")
    map_category(client, user["id"], category["id"], envelope["id"])

    allocate_payload = {"amount": "50.00", "occurred_on": date(2026, 1, 1).isoformat()}
    allocation = client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json=allocate_payload,
    ).json()

    tx_payload = {
        "type": "expense",
        "category_id": category["id"],
        "amount": "20.00",
        "occurred_on": date(2026, 1, 1).isoformat(),
        "description": "Market",
    }
    tx_response = client.post("/transactions", json=tx_payload)
    assert tx_response.status_code == 201

    period_id = allocation["allocation"]["envelope_period_id"]
    balance_response = client.get(
        f"/envelopes/{envelope['id']}/periods/{period_id}/balance",
    )
    assert balance_response.status_code == 200
    assert balance_response.json()["closing_balance"] == "30.00"
