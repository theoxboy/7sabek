from __future__ import annotations

from fastapi.testclient import TestClient


DEFAULT_PASSWORD = "Floussy2026"


def register_user(
    client: TestClient,
    email: str,
    currency: str = "EUR",
    sweep_interval_days: int = 7,
    password: str = DEFAULT_PASSWORD,
) -> dict:
    payload = {
        "email": email,
        "password": password,
        "currency": currency,
        "sweep_interval_days": sweep_interval_days,
        "first_name": "Test",
        "last_name": "User",
        "phone_number": "+212600000000",
        "birth_date": "1990-01-01",
        "country": "MA",
        "city": "Casablanca",
        "mfa_consent": True,
        "defer_onboarding_v2": True,
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    return response.json()


def login_user(
    client: TestClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> None:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
