from __future__ import annotations

from fastapi.testclient import TestClient


def _register_payload(password: str, email: str = "policy@example.com") -> dict:
    return {
        "email": email,
        "password": password,
        "currency": "MAD",
        "sweep_interval_days": 7,
        "first_name": "Test",
        "last_name": "Policy",
        "phone_number": "+212600000000",
        "birth_date": "1990-01-01",
        "country": "Maroc",
        "city": "Casablanca",
        "mfa_consent": True,
        "defer_onboarding_v2": True,
    }


def test_register_rejects_password_without_letter(client: TestClient) -> None:
    response = client.post("/auth/register", json=_register_payload("12345678"))
    assert response.status_code == 400
    assert "lettre" in response.json()["detail"].lower()


def test_register_rejects_password_without_digit(client: TestClient) -> None:
    response = client.post("/auth/register", json=_register_payload("Password"))
    assert response.status_code == 400
    assert "chiffre" in response.json()["detail"].lower()


def test_register_rejects_compromised_password(client: TestClient) -> None:
    response = client.post("/auth/register", json=_register_payload("password123"))
    assert response.status_code == 400
    assert "compromis" in response.json()["detail"].lower()


def test_register_accepts_password_with_easy_policy(client: TestClient) -> None:
    response = client.post("/auth/register", json=_register_payload("Budget2026"))
    assert response.status_code == 201


def test_force_reset_rejects_compromised_password(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json=_register_payload("Budget2026", email="force-reset@example.com"),
    )
    assert response.status_code == 201

    force_reset = client.post(
        "/auth/force-reset",
        json={
            "email": "force-reset@example.com",
            "current_password": "Budget2026",
            "new_password": "password123",
        },
    )
    assert force_reset.status_code == 400
    assert "compromis" in force_reset.json()["detail"].lower()
