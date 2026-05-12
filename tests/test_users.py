from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import register_user


def test_create_user_creates_default_envelope(client: TestClient) -> None:
    register_user(client, "user@example.com", sweep_interval_days=10)
    envelopes_response = client.get("/envelopes")
    assert envelopes_response.status_code == 200

    envelopes = envelopes_response.json()
    default_envelopes = [e for e in envelopes if e["is_default_savings"]]
    assert len(default_envelopes) == 1

    default = default_envelopes[0]
    assert default["name"] == "Epargnes"
    assert default["deletable"] is False
    assert default["rollover_enabled"] is True

    cash_envelopes = [e for e in envelopes if e["is_cash"]]
    assert len(cash_envelopes) == 1
    cash = cash_envelopes[0]
    assert cash["name"] == "Cash"
    assert cash["deletable"] is False
    assert cash["rollover_enabled"] is False
