from __future__ import annotations

from fastapi.testclient import TestClient

SIGNALS = {
    "platform": "Linux armv8l",
    "language": "fr-FR",
    "timezone": "Africa/Casablanca",
    "screen": "1080x2400",
    "cores": "8",
    "memory": "4",
}


def test_l2_hint_is_a_bare_boolean_and_never_restores(client: TestClient) -> None:
    # no guest yet on this "device"
    r = client.post("/auth/guest/l2-hint", json={"signals": SIGNALS})
    assert r.status_code == 200
    assert r.json() == {"maybe_exists": False}

    # create a guest carrying the same signals
    client.post("/auth/guest", json={"signals": SIGNALS})
    client.cookies.clear()  # lose the session entirely

    # now the hint says "maybe" — but returns nothing else, and no session
    r = client.post("/auth/guest/l2-hint", json={"signals": SIGNALS})
    assert r.status_code == 200
    assert r.json() == {"maybe_exists": True}
    assert client.get("/auth/me").status_code == 401  # L2 never logs you in

    # different device signals → no hint
    other = {**SIGNALS, "platform": "iPhone", "screen": "390x844"}
    assert client.post("/auth/guest/l2-hint", json={"signals": other}).json() == {
        "maybe_exists": False
    }


def test_l2_needs_at_least_three_signals(client: TestClient) -> None:
    r = client.post("/auth/guest/l2-hint", json={"signals": {"platform": "x", "language": "fr"}})
    assert r.json() == {"maybe_exists": False}
