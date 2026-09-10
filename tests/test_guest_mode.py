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
    assert not body["user"].get("email")  # the internal placeholder never leaves the API (F8)
    # Non-browser clients (the Android app) carry the session as bearer tokens.
    assert body["user"]["access_token"]
    assert body["user"]["refresh_token"]
    assert body["user"]["token_type"] == "bearer"

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
    new_token = res.json().get("guest_token")
    assert new_token is not None
    client.cookies.clear()
    assert client.post("/auth/guest/resume", json={"token": new_token}).status_code == 200


def test_recover_rejects_a_code_of_the_wrong_length(client: TestClient) -> None:
    _create_guest(client)
    client.cookies.clear()
    for bad in ("ABCD", "ABCDEFG", "ABCDEFGHJ"):  # not exactly 8 chars → 404, no DB lookup (F9)
        r = client.post("/auth/guest/recover", json={"recovery_code": bad})
        assert r.status_code == 404, (bad, r.text)


def test_claim_drops_the_recovery_code_and_l1_hashes(client: TestClient) -> None:
    body = _create_guest(client)
    code = body["recovery_code"]
    token = body["guest_token"]

    res = client.post(
        "/auth/guest/claim",
        json={"email": "f7@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200, res.text

    client.cookies.clear()
    # The recovery code and the L1 token no longer resolve to anything (F7).
    assert client.post("/auth/guest/recover", json={"recovery_code": code}).status_code == 404
    assert client.post("/auth/guest/resume", json={"token": token}).status_code == 404


def test_claim_is_an_update_no_data_moves(client: TestClient) -> None:
    body = _create_guest(client)
    guest_id = body["user"]["id"]

    env = client.post("/envelopes", json={"name": "Vacances été", "rollover_enabled": True})
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
    existing = len(client.get("/envelopes").json())  # seeded starters
    for i in range(20 - existing):
        r = client.post("/envelopes", json={"name": f"Enveloppe {i}", "rollover_enabled": True})
        assert r.status_code == 201, (i, r.text)
    over = client.post("/envelopes", json={"name": "Une de trop", "rollover_enabled": True})
    assert over.status_code == 403
    assert over.json()["detail"]["code"] == "guest_quota"


def test_guest_lands_on_a_populated_budget(client: TestClient) -> None:
    _create_guest(client)
    names = {e["name"].lower() for e in client.get("/envelopes").json()}
    for starter in ("loyer", "courses", "transport"):
        assert starter in names


def test_guest_can_log_an_expense_out_of_the_box(client: TestClient) -> None:
    _create_guest(client)
    cats = {c["name"]: c["id"] for c in client.get("/categories").json()}
    assert "groceries" in cats  # seeded + mapped to "Courses"
    tx = client.post(
        "/transactions",
        json={
            "category_id": cats["groceries"],
            "type": "expense",
            "amount": 320,
            "occurred_on": "2026-09-03",
        },
    )
    assert tx.status_code == 201, tx.text
    s = client.get("/auth/guest/summary").json()
    assert s["transaction_count"] == 1
    assert s["expense_total"] == 320.0


def test_guest_can_declare_income_and_split_it(client: TestClient) -> None:
    """A guest gets an income category too, so they can declare a salary and
    then allocate it across the envelopes — without onboarding."""
    _create_guest(client)
    cats = {c["name"]: c["id"] for c in client.get("/categories").json()}
    assert "income_general" in cats

    inc = client.post(
        "/transactions",
        json={
            "category_id": cats["income_general"],
            "type": "income",
            "amount": 6000,
            "occurred_on": "2026-09-03",
        },
    )
    assert inc.status_code == 201, inc.text

    envelopes = client.get("/envelopes").json()
    target = next(e for e in envelopes if not e.get("is_cash") and not e.get("is_default_savings"))
    alloc = client.post(
        f"/envelopes/{target['id']}/allocate-from-cash",
        json={"amount": "1500", "occurred_on": "2026-09-03"},
    )
    assert alloc.status_code in (200, 201), alloc.text


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


def test_email_code_verifies_the_code_and_is_not_a_claim(client: TestClient) -> None:
    body = _create_guest(client)
    code = body["recovery_code"]

    # wrong code → rejected, nothing sent
    bad = client.post(
        "/auth/guest/email-code", json={"email": "me@example.com", "recovery_code": "AAAA-AAAA"}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "code_mismatch"

    # right code (grouped form accepted) → ok, still a guest (not a claim)
    ok = client.post(
        "/auth/guest/email-code",
        json={"email": "me@example.com", "recovery_code": f"{code[:4]}-{code[4:]}"},
    )
    assert ok.status_code == 200, ok.text
    assert client.get("/auth/me").json()["is_guest"] is True


def test_claimed_guest_carries_claimed_at(client: TestClient) -> None:
    _create_guest(client)
    res = client.post(
        "/auth/guest/claim",
        json={"email": "claimedat@example.com", "password": DEFAULT_PASSWORD},
    )
    assert res.status_code == 200
    assert res.json()["claimed_at"] is not None
    assert res.json()["protection_level"] == 100


def test_guest_summary_reports_the_guests_own_figures(client: TestClient) -> None:
    _create_guest(client)
    client.post("/envelopes", json={"name": "Extra", "rollover_enabled": True})
    s = client.get("/auth/guest/summary")
    assert s.status_code == 200, s.text
    d = s.json()
    assert d["envelope_count"] >= 1
    assert d["transaction_count"] == 0
    assert d["days_tracking"] >= 0
    assert isinstance(d["expense_total"], (int, float))

    # a member cannot read the guest summary
    client.cookies.clear()
    register_user(client, "notguest-summary@example.com")
    assert client.get("/auth/guest/summary").status_code == 409


def test_member_is_unaffected(client: TestClient) -> None:
    register_user(client, "member@example.com")
    me = client.get("/auth/me")
    assert me.json()["is_guest"] is False
    assert me.json()["protection_level"] is None


def test_guest_cannot_mutate_account_only_features(client: TestClient) -> None:
    _create_guest(client)

    # Writes to account-only features are refused with a clear code…
    checks = [
        ("post", "/goals", {"name": "Vacances", "target_amount": 5000}),
        ("post", "/debts", {"label": "Pret", "amount": 1000}),
        ("post", "/sweeps/run", None),
        ("post", "/income-reminders", {"label": "Salaire", "day_of_month": 1}),
        ("patch", "/gamification/settings", {}),
    ]
    for method, path, body in checks:
        res = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert res.status_code == 403, (path, res.status_code, res.text)
        assert res.json()["detail"]["code"] == "guest_feature_locked", (path, res.text)

    # …but the guest may still read the page (the preview stays alive).
    assert client.get("/goals").status_code == 200
    assert client.get("/debts").status_code == 200


def test_guest_can_run_the_income_distribution_journey(client: TestClient) -> None:
    """Splitting income into envelopes is the whole point of Mode Découverte —
    distribution is not guest-locked."""
    _create_guest(client)

    # Reads and mutations both go through — no guest_feature_locked here.
    assert client.get("/distribution/rules").status_code == 200
    assert client.post("/distribution/onboarding-status", json={}).status_code == 200

    for name in ("Loyer", "Courses"):
        client.post("/envelopes", json={"name": name, "rollover_enabled": False})
    saved = client.post(
        "/distribution/configs",
        json={
            "name": "Ma répartition",
            "auto_enabled": False,
            "percent_mode": "equal",
            "rows": [],
        },
    )
    assert saved.status_code in (200, 201), saved.text


def test_member_can_still_use_account_features(client: TestClient) -> None:
    register_user(client, "goals-member@example.com")
    res = client.post("/goals", json={"name": "Vacances", "target_amount": 5000})
    assert res.status_code in (200, 201), res.text
