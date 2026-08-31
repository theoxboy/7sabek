from __future__ import annotations

from fastapi.testclient import TestClient
from tests.utils import register_user


def create_user(client: TestClient) -> dict:
    return register_user(client, "debt_user@example.com")


def test_create_envelope_rollover_restrictions(client: TestClient) -> None:
    user = create_user(client)

    # 1. Try to create a debt envelope with rollover disabled -> should fail with 400
    response = client.post(
        "/envelopes",
        json={"name": "my loans", "rollover_enabled": False},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "ENVELOPE_ROLLOVER_OFF_FORBIDDEN_FOR_PROFILE"

    # 2. Try to create a debt envelope with rollover enabled -> should succeed
    response = client.post(
        "/envelopes",
        json={"name": "my loans", "rollover_enabled": True},
    )
    assert response.status_code == 201
    env_id = response.json()["id"]

    # 3. Try to update this debt envelope to set rollover_enabled=False -> should fail with 400
    response = client.patch(
        f"/envelopes/{env_id}",
        json={"rollover_enabled": False},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "ENVELOPE_ROLLOVER_OFF_FORBIDDEN_FOR_PROFILE"

    # 4. Create a normal envelope with rollover disabled
    response = client.post(
        "/envelopes",
        json={"name": "Groceries", "rollover_enabled": False},
    )
    assert response.status_code == 201
    groceries_id = response.json()["id"]

    # 5. Renaming a normal envelope to a debt-ish name no longer auto-locks it:
    #    is_debt is an explicit flag, not re-derived from the name on every edit.
    response = client.patch(
        f"/envelopes/{groceries_id}",
        json={"name": "my credit card"},
    )
    assert response.status_code == 200
    assert response.json()["is_debt"] is False

    # 6. Marking it a debt explicitly, while rollover is off, is rejected until
    #    rollover is turned on.
    response = client.patch(f"/envelopes/{groceries_id}", json={"is_debt": True})
    assert response.status_code == 400
    assert response.json()["detail"] == "ENVELOPE_ROLLOVER_OFF_FORBIDDEN_FOR_PROFILE"
    response = client.patch(
        f"/envelopes/{groceries_id}", json={"is_debt": True, "rollover_enabled": True}
    )
    assert response.status_code == 200
    assert response.json()["is_debt"] is True


def test_is_debt_flag_is_explicit_and_correctable(client: TestClient) -> None:
    """The core fix: is_debt is a real flag, seeded from a heuristic but
    overridable — a typo'd name no longer strands the fund, a false positive
    no longer force-locks a normal envelope."""
    user = create_user(client)

    # A debt fund whose name misses every keyword can still be marked a debt.
    response = client.post(
        "/envelopes",
        json={"name": "Detes voiture", "rollover_enabled": True, "is_debt": True},
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_debt"] is True

    # "Salaf" — the app's own word for a loan — is now caught by the heuristic.
    response = client.post(
        "/envelopes", json={"name": "Salaf 3and Ahmed", "rollover_enabled": True}
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_debt"] is True

    # A false positive ("Carte de crédit" as a normal spending envelope) can be
    # opted out and then turned into a swept flexible envelope.
    response = client.post(
        "/envelopes", json={"name": "Carte de crédit", "rollover_enabled": True}
    )
    assert response.status_code == 201
    cc_id = response.json()["id"]
    assert response.json()["is_debt"] is True
    response = client.patch(
        f"/envelopes/{cc_id}", json={"is_debt": False, "rollover_enabled": False}
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_debt"] is False
    assert response.json()["rollover_enabled"] is False


def test_delete_envelope_cascades_rules(client: TestClient) -> None:
    user = create_user(client)

    # 1. Create a normal envelope
    response = client.post(
        "/envelopes",
        json={"name": "Rent Envelope", "rollover_enabled": True},
    )
    assert response.status_code == 201
    env_id = response.json()["id"]

    # 2. Create a distribution rule targeting this envelope
    rule_payload = {
        "target_type": "envelope",
        "target_id": env_id,
        "mode": "fixed_per_period",
        "amount": 120.0,
        "priority": 100,
        "rank": 1,
        "enabled": True,
        "auto_apply_on_income": True,
    }
    response = client.post("/distribution/rules", json=rule_payload)
    assert response.status_code == 201
    rule_id = response.json()["id"]

    # 3. Verify the rule exists in the active rules list
    response = client.get("/distribution/rules")
    assert response.status_code == 200
    rules = response.json()
    assert any(r["id"] == rule_id for r in rules)

    # 4. Delete the envelope
    response = client.delete(f"/envelopes/{env_id}")
    assert response.status_code == 204

    # 5. Verify the rule is cascaded and no longer exists
    response = client.get("/distribution/rules")
    assert response.status_code == 200
    rules = response.json()
    assert not any(r["id"] == rule_id for r in rules)
