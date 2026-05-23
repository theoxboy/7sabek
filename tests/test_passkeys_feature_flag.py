from __future__ import annotations


def test_passkeys_register_options_disabled_returns_404(client) -> None:
    response = client.post("/auth/passkeys/register/options")
    assert response.status_code == 404


def test_passkeys_login_options_disabled_returns_404(client) -> None:
    response = client.post("/auth/passkeys/login/options", json={"email": "user@example.com"})
    assert response.status_code == 404


def test_passkeys_list_disabled_returns_404(client) -> None:
    response = client.get("/auth/passkeys")
    assert response.status_code == 404
