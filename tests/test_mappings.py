from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import register_user, login_user


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


def test_get_mappings_empty(client: TestClient) -> None:
    user = create_user(client, "mapempty@example.com")
    response = client.get("/mappings")
    assert response.status_code == 200
    assert response.json() == []


def test_put_get_delete_mapping(client: TestClient) -> None:
    user = create_user(client, "mapcrud@example.com")
    category = create_category(client, user["id"], "Fuel")
    envelope = create_envelope(client, user["id"], "Car")

    put_response = client.put(
        f"/categories/{category['id']}/envelope",
        json={"envelope_id": envelope["id"]},
    )
    assert put_response.status_code == 200

    list_response = client.get("/mappings")
    assert list_response.status_code == 200
    data = list_response.json()
    assert len(data) == 1
    assert data[0]["category_id"] == category["id"]
    assert data[0]["envelope_id"] == envelope["id"]

    delete_response = client.delete(
        f"/categories/{category['id']}/envelope"
    )
    assert delete_response.status_code == 204

    list_again = client.get("/mappings")
    assert list_again.status_code == 200
    assert list_again.json() == []


def test_mapping_rejects_cross_user_entities(client: TestClient) -> None:
    user_a = create_user(client, "mapusera@example.com")
    user_b = create_user(client, "mapuserb@example.com")

    category_a = create_category(client, user_a["id"], "Bills")
    envelope_b = create_envelope(client, user_b["id"], "Other")

    login_user(client, "mapusera@example.com")

    response = client.put(
        f"/categories/{category_a['id']}/envelope",
        json={"envelope_id": envelope_b["id"]},
    )
    assert response.status_code == 404
