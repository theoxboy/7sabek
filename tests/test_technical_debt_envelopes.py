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

    # 5. Try to rename this normal envelope to a name with debt keyword -> should fail with 400
    response = client.patch(
        f"/envelopes/{groceries_id}",
        json={"name": "my credit card"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "ENVELOPE_ROLLOVER_OFF_FORBIDDEN_FOR_PROFILE"


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
