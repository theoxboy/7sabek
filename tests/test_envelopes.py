from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import register_user


def create_user(client: TestClient) -> dict:
    return register_user(client, "user@example.com")


def test_duplicate_envelope_name_returns_400(client: TestClient) -> None:
    user = create_user(client)

    payload = {"name": "Groceries", "rollover_enabled": False}
    first = client.post("/envelopes", json=payload)
    assert first.status_code == 201

    second = client.post("/envelopes", json=payload)
    assert second.status_code == 400


def test_delete_default_savings_returns_400(client: TestClient) -> None:
    user = create_user(client)

    envelopes = client.get("/envelopes")
    assert envelopes.status_code == 200

    default_env = next(e for e in envelopes.json() if e["is_default_savings"])
    response = client.delete(f"/envelopes/{default_env['id']}")
    assert response.status_code == 400


def test_list_envelopes_requires_user_header(client: TestClient) -> None:
    response = client.get("/envelopes")
    assert response.status_code == 401
