from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import register_user


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def test_patch_settings_currency_only(client: TestClient) -> None:
    user = create_user(client, "settings-currency@example.com")
    response = client.patch("/users/me/settings", json={"currency": "USD"})
    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "USD"
    assert data["sweep_interval_days"] == 7
    assert data["auto_distribution_enabled"] is False
    assert data["auto_sweep_enabled"] is True


def test_patch_settings_sweep_only(client: TestClient) -> None:
    user = create_user(client, "settings-sweep@example.com")
    response = client.patch("/users/me/settings", json={"sweep_interval_days": 14})
    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "EUR"
    assert data["sweep_interval_days"] == 14
    assert data["auto_distribution_enabled"] is False
    assert data["auto_sweep_enabled"] is True


def test_patch_settings_auto_distribution_toggle(client: TestClient) -> None:
    user = create_user(client, "settings-dist@example.com")
    response = client.patch(
        "/users/me/settings", json={"auto_distribution_enabled": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["auto_distribution_enabled"] is True
    assert data["auto_sweep_enabled"] is True


def test_patch_settings_auto_sweep_toggle(client: TestClient) -> None:
    user = create_user(client, "settings-auto-sweep@example.com")
    response = client.patch("/users/me/settings", json={"auto_sweep_enabled": False})
    assert response.status_code == 200
    data = response.json()
    assert data["auto_sweep_enabled"] is False
