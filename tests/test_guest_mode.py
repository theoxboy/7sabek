from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import DEFAULT_PASSWORD, register_user


def _create_guest(client: TestClient, idem: str | None = None) -> dict:
    headers = {"Idempotency-Key": idem} if idem else {}
    res = client.post("/auth/guest", json={}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_guest_is_created_and_authenticated(client: TestClient) -> None:
    body = _create_guest(client)
    assert body["guest_token"]
    assert body["recovery_code"]
    assert body["user"]["is_guest"] is True
    assert body["user"]["protection_level"] == 40
    assert body["user"]["email"].endswith("@guests.7sabek.ma")  # internal placeholder

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]
    assert me.json()["is_guest"] is True


def test_idempotency_key_replays_the_same_guest(client: TestClient) -> None:
    first = _create_guest(client, idem="abc-123")
    second = _create_guest(client, idem="abc-123")
    assert first["user"]["id"] == second["user"]["id"]
    assert first["guest_token"] == second["guest_token"]

    other = _create_guest(client, idem="different-key")
    assert other["user"]["id"] != first["user"]["id"]


def test_resume_exchanges_a_mirror_token_for_a_session(client: TestClient) -> None:
    body = _create_guest(client)
    token = body["guest_token"]
    client.cookies.clear()  # lose the session

    res = client.post("/auth/guest/resume", json={"token": token})
    assert res.status_code == 200
    assert res.json()["user"]["id"] == body["user"]["id"]
    assert client.get("/auth/me").status_code == 200

    client.cookies.clear()
    assert client.post("/auth/guest/resume", json={"token": "not-a-real-token"}).status_code == 404


def test_recover_with_the_recovery_code(client: TestClient) -> None:
    body = _create_guest(client)
    code = body["recovery_code"]
    client.cookies.clear()

    # accept the code however it is typed
    res = client.post("/auth/guest/recover", json={"recovery_code": f"{code[:4]}-{code[4:]}".lower()})
    assert res.status_code == 200
    assert res.json()["user"]["id"] == body["user"]["id"]


def test_claim_is_an_update_no_data_moves(client: TestClient) -> None:
    body = _create_guest(client)
    guest_id = body["user"]["id"]

    env = client.post("/envelopes", json={"name": "Courses", "rollover_enabled": True})
    assert env.status_code == 201, env.text
    envelope_id = env.json()["id"]

    res = client.post(
        "/auth/guest/claim",
        json={"email": "claimed@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == guest_id  # same row
    assert res.json()["is_guest"] is False
    assert res.json()["protection_level"] == 100

    # the envelope created as a guest is still there, same owner
    listed = client.get("/envelopes")
    assert listed.status_code == 200
    assert any(e["id"] == envelope_id for e in listed.json())

    # and the account now logs in normally
    client.cookies.clear()
    login = client.post(
        "/auth/login", json={"email": "claimed@example.com", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200


def test_claim_rejects_an_email_already_taken(client: TestClient) -> None:
    register_user(client, "taken@example.com")
    client.cookies.clear()
    _create_guest(client)

    res = client.post(
        "/auth/guest/claim",
        json={"email": "taken@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "email_taken"
    assert client.get("/auth/me").json()["is_guest"] is True  # still a guest


def test_delete_guest_erases_everything(client: TestClient) -> None:
    _create_guest(client)
    client.post("/envelopes", json={"name": "Loyer", "rollover_enabled": True})

    res = client.delete("/auth/guest")
    assert res.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_envelope_quota_caps_a_guest_at_20(client: TestClient) -> None:
    _create_guest(client)
    for i in range(20):
        r = client.post("/envelopes", json={"name": f"Enveloppe {i}", "rollover_enabled": True})
        assert r.status_code == 201, (i, r.text)
    over = client.post("/envelopes", json={"name": "Enveloppe 21", "rollover_enabled": True})
    assert over.status_code == 403
    assert over.json()["detail"]["code"] == "guest_quota"


def test_ack_recovery_moves_protection_40_to_70(client: TestClient) -> None:
    body = _create_guest(client)
    assert body["user"]["protection_level"] == 40
    assert body["user"]["recovery_code_ack"] is False

    res = client.post("/auth/guest/ack-recovery")
    assert res.status_code == 200, res.text
    assert res.json()["protection_level"] == 70
    assert res.json()["recovery_code_ack"] is True

    # idempotent
    assert client.post("/auth/guest/ack-recovery").json()["protection_level"] == 70


def test_claimed_guest_carries_claimed_at(client: TestClient) -> None:
    _create_guest(client)
    res = client.post(
        "/auth/guest/claim",
        json={"email": "claimedat@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200
    assert res.json()["claimed_at"] is not None
    assert res.json()["protection_level"] == 100


def test_member_is_unaffected(client: TestClient) -> None:
    register_user(client, "member@example.com")
    me = client.get("/auth/me")
    assert me.json()["is_guest"] is False
    assert me.json()["protection_level"] is None
