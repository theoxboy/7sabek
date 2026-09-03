from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import DEFAULT_PASSWORD, register_user


def _guest_with_expenses(client: TestClient, amounts: list[int]) -> None:
    client.post("/auth/guest", json={})
    cats = {c["name"]: c["id"] for c in client.get("/categories").json()}
    for amt in amounts:
        r = client.post(
            "/transactions",
            json={
                "category_id": cats["groceries"],
                "type": "expense",
                "amount": amt,
                "occurred_on": "2026-09-03",
            },
        )
        assert r.status_code == 201, r.text


def _expense_total(client: TestClient) -> float:
    total = 0.0
    for t in client.get("/transactions").json():
        if t["type"] == "expense":
            total += float(t["amount"])
    return total


def test_merge_replays_guest_expenses_onto_the_existing_account(client: TestClient) -> None:
    register_user(client, "target@example.com")
    before = _expense_total(client)
    client.cookies.clear()

    _guest_with_expenses(client, [100, 250, 75])

    res = client.post(
        "/auth/guest/merge",
        json={"email": "target@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200, res.text
    assert res.json()["transactions_merged"] == 3
    assert res.json()["user"]["email"] == "target@example.com"
    assert res.json()["user"]["is_guest"] is False

    # session is now the target account, and the money arrived
    assert client.get("/auth/me").json()["email"] == "target@example.com"
    assert _expense_total(client) == before + 425.0


def test_merge_rejects_wrong_password(client: TestClient) -> None:
    register_user(client, "target2@example.com")
    client.cookies.clear()
    _guest_with_expenses(client, [50])

    res = client.post(
        "/auth/guest/merge",
        json={"email": "target2@example.com", "password": "wrong-password"},
    )
    assert res.status_code == 401
    assert client.get("/auth/me").json()["is_guest"] is True  # still a guest


def test_merge_with_no_expenses_still_signs_into_the_account(client: TestClient) -> None:
    register_user(client, "target3@example.com")
    client.cookies.clear()
    client.post("/auth/guest", json={})

    res = client.post(
        "/auth/guest/merge",
        json={"email": "target3@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200
    assert res.json()["transactions_merged"] == 0
    assert client.get("/auth/me").json()["email"] == "target3@example.com"
