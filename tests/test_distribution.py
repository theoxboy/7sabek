from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi.testclient import TestClient

from tests.utils import register_user


def create_user(client: TestClient, email: str) -> dict:
    return register_user(client, email)


def create_category(client: TestClient, name: str) -> dict:
    response = client.post("/categories", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_envelope(client: TestClient, name: str) -> dict:
    response = client.post("/envelopes", json={"name": name, "rollover_enabled": False})
    assert response.status_code == 201
    return response.json()


def set_auto_distribution(client: TestClient, enabled: bool) -> None:
    response = client.patch(
        "/users/me/settings", json={"auto_distribution_enabled": enabled}
    )
    assert response.status_code == 200


def create_rule(
    client: TestClient,
    *,
    target_type: str,
    target_id: str,
    mode: str,
    amount: Optional[str] = None,
    percent: Optional[str] = None,
    rank: int = 1,
    enabled: bool = True,
    auto_apply_on_income: bool = True,
) -> dict:
    payload = {
        "target_type": target_type,
        "target_id": target_id,
        "mode": mode,
        "rank": rank,
        "enabled": enabled,
        "auto_apply_on_income": auto_apply_on_income,
    }
    if amount is not None:
        payload["amount"] = amount
    if percent is not None:
        payload["percent"] = percent
    response = client.post("/distribution/rules", json=payload)
    assert response.status_code == 201
    return response.json()


def create_income(client: TestClient, category_id: str, amount: str) -> dict:
    response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": category_id,
            "amount": amount,
            "occurred_on": date.today().isoformat(),
            "description": "Income",
        },
    )
    assert response.status_code == 201
    return response.json()


def envelope_closing_balance(dashboard: dict, envelope_name: str) -> Decimal:
    for item in dashboard["envelopes"]:
        if item["envelope"]["name"] == envelope_name:
            return Decimal(str(item["balance"]["closing_balance"]))
    raise AssertionError(f"Envelope not found: {envelope_name}")


def create_saved_config(
    client: TestClient,
    *,
    name: str,
    auto_enabled: bool,
) -> dict:
    response = client.post(
        "/distribution/configs",
        json={
            "name": name,
            "auto_enabled": auto_enabled,
            "percent_mode": "equal",
            "rows": [],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_income_auto_distribution_fixed_then_percent(client: TestClient) -> None:
    create_user(client, "dist-fixed-percent@example.com")
    loyer = create_envelope(client, "Loyer")
    food = create_envelope(client, "Food")
    savings = create_envelope(client, "Savings")
    fun = create_envelope(client, "Fun")
    salary = create_category(client, "Salary")
    set_auto_distribution(client, True)

    create_rule(
        client,
        target_type="envelope",
        target_id=loyer["id"],
        mode="fixed",
        amount="600.00",
        rank=1,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=food["id"],
        mode="fixed",
        amount="200.00",
        rank=2,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=savings["id"],
        mode="percent",
        percent="50",
        rank=3,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=fun["id"],
        mode="percent",
        percent="50",
        rank=4,
    )

    create_income(client, salary["id"], "1000.00")

    dashboard = client.get("/dashboard").json()
    assert dashboard["cash_balance"] in {"0.00", "0", "0.0"}
    assert envelope_closing_balance(dashboard, "Loyer") in {
        Decimal("600.00"),
        Decimal("600"),
    }
    assert envelope_closing_balance(dashboard, "Food") in {
        Decimal("200.00"),
        Decimal("200"),
    }
    assert envelope_closing_balance(dashboard, "Savings") in {
        Decimal("100.00"),
        Decimal("100"),
    }
    assert envelope_closing_balance(dashboard, "Fun") in {
        Decimal("100.00"),
        Decimal("100"),
    }


def test_income_insufficient_for_fixes(client: TestClient) -> None:
    create_user(client, "dist-insufficient-fixed@example.com")
    loyer = create_envelope(client, "Loyer")
    food = create_envelope(client, "Food")
    savings = create_envelope(client, "Savings")
    salary = create_category(client, "Salary")
    set_auto_distribution(client, True)

    create_rule(
        client,
        target_type="envelope",
        target_id=loyer["id"],
        mode="fixed",
        amount="600.00",
        rank=1,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=food["id"],
        mode="fixed",
        amount="200.00",
        rank=2,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=savings["id"],
        mode="percent",
        percent="50",
        rank=3,
    )

    create_income(client, salary["id"], "500.00")

    dashboard = client.get("/dashboard").json()
    assert dashboard["cash_balance"] in {"0.00", "0", "0.0"}
    assert envelope_closing_balance(dashboard, "Loyer") in {
        Decimal("500.00"),
        Decimal("500"),
    }
    assert envelope_closing_balance(dashboard, "Food") in {
        Decimal("0.00"),
        Decimal("0"),
    }
    assert envelope_closing_balance(dashboard, "Savings") in {
        Decimal("0.00"),
        Decimal("0"),
    }


def test_percent_over_100_normalized(client: TestClient) -> None:
    create_user(client, "dist-percent-normalized@example.com")
    savings = create_envelope(client, "Savings")
    fun = create_envelope(client, "Fun")
    salary = create_category(client, "Salary")
    set_auto_distribution(client, True)

    create_rule(
        client,
        target_type="envelope",
        target_id=savings["id"],
        mode="percent",
        percent="70",
        rank=1,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=fun["id"],
        mode="percent",
        percent="70",
        rank=2,
    )
    create_income(client, salary["id"], "1000.00")

    dashboard = client.get("/dashboard").json()
    assert dashboard["cash_balance"] in {"0.00", "0", "0.0"}
    assert envelope_closing_balance(dashboard, "Savings") in {
        Decimal("500.00"),
        Decimal("500"),
    }
    assert envelope_closing_balance(dashboard, "Fun") in {
        Decimal("500.00"),
        Decimal("500"),
    }


def test_rank_order_applies_fixed_in_rank_order(client: TestClient) -> None:
    create_user(client, "dist-rank-order@example.com")
    alpha = create_envelope(client, "Alpha")
    beta = create_envelope(client, "Beta")
    gamma = create_envelope(client, "Gamma")
    salary = create_category(client, "Salary")
    set_auto_distribution(client, True)

    create_rule(
        client,
        target_type="envelope",
        target_id=alpha["id"],
        mode="fixed",
        amount="400.00",
        rank=2,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=beta["id"],
        mode="fixed",
        amount="400.00",
        rank=1,
    )
    create_rule(
        client,
        target_type="envelope",
        target_id=gamma["id"],
        mode="fixed",
        amount="400.00",
        rank=3,
    )

    create_income(client, salary["id"], "500.00")

    dashboard = client.get("/dashboard").json()
    assert dashboard["cash_balance"] in {"0.00", "0", "0.0"}
    assert envelope_closing_balance(dashboard, "Beta") in {
        Decimal("400.00"),
        Decimal("400"),
    }
    assert envelope_closing_balance(dashboard, "Alpha") in {
        Decimal("100.00"),
        Decimal("100"),
    }
    assert envelope_closing_balance(dashboard, "Gamma") in {
        Decimal("0.00"),
        Decimal("0"),
    }


def test_revert_to_onboarding_baseline_uses_onboarding_source_when_present(
    client: TestClient,
) -> None:
    create_user(client, "dist-revert-baseline@example.com")
    baseline = create_saved_config(client, name="baseline", auto_enabled=False)
    current = create_saved_config(client, name="current", auto_enabled=True)
    effective = (date.today() + timedelta(days=1)).isoformat()

    response = client.post(
        f"/distribution/configs/{current['id']}/revert-onboarding-baseline",
        json={"effective_from_period_start": effective},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "post_onboarding_adjustment"
    assert payload["auto_enabled"] == baseline["auto_enabled"]
    assert payload["effective_from_period_start"] == effective


def test_revert_to_onboarding_baseline_falls_back_when_onboarding_source_missing(
    client: TestClient,
) -> None:
    create_user(client, "dist-revert-fallback@example.com")
    baseline = create_saved_config(client, name="baseline", auto_enabled=False)
    current = create_saved_config(client, name="current", auto_enabled=True)
    delete_response = client.delete(f"/distribution/configs/{baseline['id']}")
    assert delete_response.status_code == 204
    effective = (date.today() + timedelta(days=1)).isoformat()

    response = client.post(
        f"/distribution/configs/{current['id']}/revert-onboarding-baseline",
        json={"effective_from_period_start": effective},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "post_onboarding_adjustment"
    assert payload["auto_enabled"] is True
    assert payload["effective_from_period_start"] == effective


def test_apply_next_cycle_uses_effective_fixed_rules_when_config_has_no_fixed_rows(
    client: TestClient,
) -> None:
    create_user(client, "dist-apply-no-fixed@example.com")
    cfg = create_saved_config(client, name="percent-only", auto_enabled=True)
    debt_env = create_envelope(client, "Debt Sandbox")
    create_rule(
        client,
        target_type="envelope",
        target_id=debt_env["id"],
        mode="fixed",
        amount="100.00",
        rank=1,
    )
    effective = (date.today() + timedelta(days=1)).isoformat()

    response = client.post(
        f"/distribution/configs/{cfg['id']}/apply-next-cycle",
        json={
            "cut1_pct": 25,
            "cut2_pct": 50,
            "effective_from_period_start": effective,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    fixed_rows = [
        row
        for row in payload.get("rows", [])
        if row.get("enabled") and row.get("mode") == "fixed"
    ]
    assert fixed_rows
