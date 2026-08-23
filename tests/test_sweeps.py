from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi.testclient import TestClient

from tests.utils import register_user

from app.services.periods import period_bounds


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def create_user(client: TestClient, email: str, sweep_days: int = 7) -> dict:
    return register_user(client, email, sweep_interval_days=sweep_days)


def create_envelope(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/envelopes",
        json={"name": name, "rollover_enabled": False},
    )
    assert response.status_code == 201
    return response.json()


def create_category(client: TestClient, user_id: str, name: str) -> dict:
    response = client.post(
        "/categories",
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()


def map_category(
    client: TestClient, user_id: str, category_id: str, envelope_id: str
) -> None:
    response = client.put(
        f"/categories/{category_id}/envelope",
        json={"envelope_id": envelope_id},
    )
    assert response.status_code == 200


def fetch_period(
    database_url: str,
    envelope_id: str,
    period_start: date,
    period_end: date,
) -> Optional[dict]:
    async def _fetch() -> Optional[dict]:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                """
                SELECT id, opening_balance
                FROM envelope_periods
                WHERE envelope_id = $1 AND period_start = $2 AND period_end = $3
                """,
                envelope_id,
                period_start,
                period_end,
            )
            if row is None:
                return None
            return {
                "id": str(row["id"]),
                "opening_balance": str(row["opening_balance"]),
            }
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def fetch_default_savings_id(database_url: str, user_id: str) -> str:
    async def _fetch() -> str:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                "SELECT id FROM envelopes WHERE user_id = $1 AND is_default_savings = true",
                user_id,
            )
            return str(row["id"])
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def fetch_sweep_amount(database_url: str, from_period_id: str) -> Optional[str]:
    async def _fetch() -> Optional[str]:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            row = await conn.fetchrow(
                "SELECT amount FROM sweeps WHERE from_envelope_period_id = $1",
                from_period_id,
            )
            return str(row["amount"]) if row else None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def fetch_user_anchor_date(database_url: str, user_id: str) -> date:
    async def _fetch() -> date:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            value = await conn.fetchval(
                "SELECT created_at::date FROM users WHERE id = $1",
                user_id,
            )
            return value
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def set_user_anchor_date(database_url: str, user_id: str, anchor_date: date) -> None:
    async def _update() -> None:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            await conn.execute(
                "UPDATE users SET created_at = $2::timestamp WHERE id = $1",
                user_id,
                anchor_date,
            )
        finally:
            await conn.close()

    asyncio.run(_update())


def fetch_sweeps_count_for_date(database_url: str, user_id: str, swept_on: date) -> int:
    async def _fetch() -> int:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            value = await conn.fetchval(
                "SELECT count(*) FROM sweeps WHERE user_id = $1 AND swept_on = $2",
                user_id,
                swept_on,
            )
            return int(value or 0)
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def fetch_swept_on_dates(database_url: str, user_id: str) -> list[date]:
    async def _fetch() -> list[date]:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            rows = await conn.fetch(
                "SELECT DISTINCT swept_on FROM sweeps WHERE user_id = $1 ORDER BY swept_on ASC",
                user_id,
            )
            return [row["swept_on"] for row in rows]
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_sweep_moves_leftover_to_savings(client: TestClient, database_url: str) -> None:
    user = create_user(client, "sweep-rule@example.com")

    envelope_a = create_envelope(client, user["id"], "Envelope A")
    envelope_b = create_envelope(client, user["id"], "Envelope B")

    category_a = create_category(client, user["id"], "Cat A")
    category_b = create_category(client, user["id"], "Cat B")

    map_category(client, user["id"], category_a["id"], envelope_a["id"])
    map_category(client, user["id"], category_b["id"], envelope_b["id"])

    anchor = fetch_user_anchor_date(database_url, user["id"])
    period_start, period_end = period_bounds(
        anchor, anchor + timedelta(days=1), user["sweep_interval_days"]
    )
    occurred_on = period_start + timedelta(days=1)

    client.post(
        f"/envelopes/{envelope_a['id']}/allocate",
        json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
    )
    client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_a["id"],
            "amount": "30.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Expense A",
        },
    )

    client.post(
        f"/envelopes/{envelope_b['id']}/allocate",
        json={"amount": "50.00", "occurred_on": occurred_on.isoformat()},
    )
    client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category_b["id"],
            "amount": "50.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Expense B",
        },
    )

    sweep_response = client.post(
        "/sweeps/run", json={"as_of": period_end.isoformat()}
    )
    assert sweep_response.status_code == 200

    period_a = fetch_period(database_url, envelope_a["id"], period_start, period_end)
    assert period_a is not None
    balance_a = client.get(
        f"/envelopes/{envelope_a['id']}/periods/{period_a['id']}/balance",
    )
    assert balance_a.status_code == 200
    assert balance_a.json()["closing_balance"] == "0.00"

    period_b = fetch_period(database_url, envelope_b["id"], period_start, period_end)
    assert period_b is not None
    balance_b = client.get(
        f"/envelopes/{envelope_b['id']}/periods/{period_b['id']}/balance",
    )
    assert balance_b.status_code == 200
    assert balance_b.json()["closing_balance"] == "0.00"

    sweep_amount = fetch_sweep_amount(database_url, period_a["id"])
    assert sweep_amount == "70.00"

    savings_id = fetch_default_savings_id(database_url, user["id"])
    savings_period = fetch_period(database_url, savings_id, period_start, period_end)
    assert savings_period is not None
    balance_savings = client.get(
        f"/envelopes/{savings_id}/periods/{savings_period['id']}/balance",
    )
    assert balance_savings.status_code == 200
    assert balance_savings.json()["closing_balance"] == "70.00"

    next_start, next_end = period_bounds(
        anchor, period_end, user["sweep_interval_days"]
    )
    next_a = fetch_period(database_url, envelope_a["id"], next_start, next_end)
    next_b = fetch_period(database_url, envelope_b["id"], next_start, next_end)
    assert next_a is not None
    assert next_a["opening_balance"] == "0.00"
    assert next_b is not None
    assert next_b["opening_balance"] == "0.00"


def test_sweep_endpoint_alias(client: TestClient, database_url: str) -> None:
    user = create_user(client, "sweep-alias@example.com")

    anchor = fetch_user_anchor_date(database_url, user["id"])
    _period_start, period_end = period_bounds(
        anchor, anchor + timedelta(days=1), user["sweep_interval_days"]
    )

    response = client.post(
        "/sweeps", json={"as_of": period_end.isoformat()}
    )
    assert response.status_code == 200
    data = response.json()
    assert "periods_swept" in data
    assert "sweeps_created" in data


def test_auto_sweep_runs_on_login_when_due(client: TestClient, database_url: str) -> None:
    user = create_user(client, "sweep-auto-login@example.com", sweep_days=1)
    set_user_anchor_date(database_url, user["id"], date.today() - timedelta(days=3))
    anchor = fetch_user_anchor_date(database_url, user["id"])

    period_start, period_end = period_bounds(
        anchor, date.today() - timedelta(days=1), user["sweep_interval_days"]
    )
    occurred_on = period_start

    envelope = create_envelope(client, user["id"], "Auto Sweep Envelope")
    category = create_category(client, user["id"], "Auto Sweep Category")
    map_category(client, user["id"], category["id"], envelope["id"])

    allocate_response = client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
    )
    assert allocate_response.status_code == 201
    expense_response = client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category["id"],
            "amount": "30.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Expense due period",
        },
    )
    assert expense_response.status_code == 201
    income_category = create_category(client, user["id"], "income_general")
    income_response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category["id"],
            "amount": "500.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Income due period",
        },
    )
    assert income_response.status_code == 201

    login_response = client.post(
        "/auth/login",
        json={"email": "sweep-auto-login@example.com", "password": "Floussy2026"},
    )
    assert login_response.status_code == 200
    assert fetch_sweeps_count_for_date(database_url, user["id"], period_end) >= 1


def test_auto_sweep_respects_user_toggle(client: TestClient, database_url: str) -> None:
    user = create_user(client, "sweep-auto-disabled@example.com", sweep_days=1)
    set_user_anchor_date(database_url, user["id"], date.today() - timedelta(days=3))
    anchor = fetch_user_anchor_date(database_url, user["id"])

    period_start, period_end = period_bounds(
        anchor, date.today() - timedelta(days=1), user["sweep_interval_days"]
    )
    occurred_on = period_start

    envelope = create_envelope(client, user["id"], "Auto Sweep Disabled Envelope")
    category = create_category(client, user["id"], "Auto Sweep Disabled Category")
    map_category(client, user["id"], category["id"], envelope["id"])

    toggle_response = client.patch(
        "/users/me/settings",
        json={"auto_sweep_enabled": False},
    )
    assert toggle_response.status_code == 200

    client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
    )
    client.post(
        "/transactions",
        json={
            "type": "expense",
            "category_id": category["id"],
            "amount": "30.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Expense due period",
        },
    )
    income_category = create_category(client, user["id"], "income_general")
    client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category["id"],
            "amount": "500.00",
            "occurred_on": occurred_on.isoformat(),
            "description": "Income due period",
        },
    )

    login_response = client.post(
        "/auth/login",
        json={"email": "sweep-auto-disabled@example.com", "password": "Floussy2026"},
    )
    assert login_response.status_code == 200
    assert fetch_sweeps_count_for_date(database_url, user["id"], period_end) == 0


def test_auto_sweep_processes_due_backlog_periods(client: TestClient, database_url: str) -> None:
    user = create_user(client, "sweep-auto-backlog@example.com", sweep_days=1)
    set_user_anchor_date(database_url, user["id"], date.today() - timedelta(days=5))
    anchor = fetch_user_anchor_date(database_url, user["id"])

    envelope = create_envelope(client, user["id"], "Backlog Envelope")
    expense_category = create_category(client, user["id"], "Backlog Expense")
    income_category = create_category(client, user["id"], "income_general")
    map_category(client, user["id"], expense_category["id"], envelope["id"])

    day_one = date.today() - timedelta(days=3)
    day_two = date.today() - timedelta(days=2)

    for occurred_on in (day_one, day_two):
        alloc = client.post(
            f"/envelopes/{envelope['id']}/allocate",
            json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
        )
        assert alloc.status_code == 201
        exp = client.post(
            "/transactions",
            json={
                "type": "expense",
                "category_id": expense_category["id"],
                "amount": "20.00",
                "occurred_on": occurred_on.isoformat(),
                "description": "Backlog expense",
            },
        )
        assert exp.status_code == 201
        inc = client.post(
            "/transactions",
            json={
                "type": "income",
                "category_id": income_category["id"],
                "amount": "300.00",
                "occurred_on": occurred_on.isoformat(),
                "description": "Backlog income",
            },
        )
        assert inc.status_code == 201

    login_response = client.post(
        "/auth/login",
        json={"email": "sweep-auto-backlog@example.com", "password": "Floussy2026"},
    )
    assert login_response.status_code == 200

    due_end_one = period_bounds(anchor, day_one, user["sweep_interval_days"])[1]
    due_end_two = period_bounds(anchor, day_two, user["sweep_interval_days"])[1]
    swept_dates = fetch_swept_on_dates(database_url, user["id"])
    assert due_end_one in swept_dates
    assert due_end_two in swept_dates


def insert_onboarding_record(database_url: str, user_id: str, payload: dict) -> None:
    import json
    from uuid import uuid4
    async def _insert() -> None:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            await conn.execute(
                """
                INSERT INTO onboarding_v2_records (id, user_id, flow_version, stage, payload, created_at, updated_at)
                VALUES ($1, $2, 'v2', 'completed', $3, now(), now())
                """,
                uuid4(),
                UUID(user_id),
                json.dumps(payload),
            )
        finally:
            await conn.close()

    asyncio.run(_insert())


def fetch_onboarding_payload(database_url: str, user_id: str) -> Optional[dict]:
    import json
    async def _fetch() -> Optional[dict]:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            val = await conn.fetchval(
                "SELECT payload FROM onboarding_v2_records WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
                UUID(user_id)
            )
            return json.loads(val) if val else None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_force_close_early_payday(client: TestClient, database_url: str) -> None:
    from uuid import UUID
    import json

    # 1. Create a user with 30-day sweeps
    user = create_user(client, "early-payday@example.com", sweep_days=30)
    user_id = user["id"]

    # Set anchor date to 2026-06-01
    anchor_date = date(2026, 6, 1)
    set_user_anchor_date(database_url, user_id, anchor_date)

    # Insert onboarding record with sweep_anchor_date set to 2026-06-01
    insert_onboarding_record(database_url, user_id, {"sweep_anchor_date": "2026-06-01"})

    # Setup category and envelope
    envelope = create_envelope(client, user_id, "Food")
    category = create_category(client, user_id, "Food Expense")
    map_category(client, user_id, category["id"], envelope["id"])

    # Allocate some money in the standard period (2026-06-01 to 2026-07-01)
    occurred_on = date(2026, 6, 15)
    client.post(
        f"/envelopes/{envelope['id']}/allocate",
        json={"amount": "100.00", "occurred_on": occurred_on.isoformat()},
    )

    # Resolve income category (primary income like income_salary)
    income_category = create_category(client, user_id, "income_salary")

    # Post early salary income on 2026-06-25 (normally expected on 2026-07-01)
    # Scenario A: Temporary payday shift (permanent_shift = False)
    response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category["id"],
            "amount": "5000.00",
            "occurred_on": "2026-06-25",
            "description": "Early salary",
            "permanent_shift": False,
        }
    )
    assert response.status_code == 201

    # Verify that the active period end got shifted to early_date (2026-06-25)
    # Check that a sweep was created on 2026-06-25
    sweeps_count = fetch_sweeps_count_for_date(database_url, user_id, date(2026, 6, 25))
    assert sweeps_count > 0

    # Under temporary shift, onboarding sweep_anchor_date must remain unchanged ("2026-06-01")
    payload = fetch_onboarding_payload(database_url, user_id)
    assert payload is not None
    assert payload.get("sweep_anchor_date") == "2026-06-01"

    # Now let's test a Permanent payday shift (permanent_shift = True)
    # Create another user for isolation
    user_perm = create_user(client, "early-payday-perm@example.com", sweep_days=30)
    user_perm_id = user_perm["id"]

    # Log in as user_perm so we can create categories/envelopes and transactions
    from tests.utils import login_user
    login_user(client, "early-payday-perm@example.com")

    set_user_anchor_date(database_url, user_perm_id, anchor_date)
    insert_onboarding_record(database_url, user_perm_id, {"sweep_anchor_date": "2026-06-01"})

    envelope_perm = create_envelope(client, user_perm_id, "Clothes")
    category_perm = create_category(client, user_perm_id, "Clothes Expense")
    map_category(client, user_perm_id, category_perm["id"], envelope_perm["id"])

    # Allocate some money
    client.post(
        f"/envelopes/{envelope_perm['id']}/allocate",
        json={"amount": "150.00", "occurred_on": occurred_on.isoformat()},
    )

    income_category_perm = create_category(client, user_perm_id, "income_salary")

    # Post early salary income on 2026-06-25 with permanent_shift = True
    response_perm = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category_perm["id"],
            "amount": "6000.00",
            "occurred_on": "2026-06-25",
            "description": "New permanent salary date",
            "permanent_shift": True,
        }
    )
    assert response_perm.status_code == 201

    # Check that a sweep was created on 2026-06-25
    sweeps_count_perm = fetch_sweeps_count_for_date(database_url, user_perm_id, date(2026, 6, 25))
    assert sweeps_count_perm > 0

    # Under permanent shift, onboarding sweep_anchor_date must be updated to "2026-06-25"
    payload_perm = fetch_onboarding_payload(database_url, user_perm_id)
    assert payload_perm is not None
    assert payload_perm.get("sweep_anchor_date") == "2026-06-25"


def insert_envelope_period_with_movement(
    database_url: str,
    user_id: str,
    envelope_id: str,
    period_start: date,
    period_end: date,
    amount: str,
) -> str:
    """Directly seed an EnvelopePeriod (+ a funding movement) whose period_start
    diverges from another envelope's period_start, simulating two envelopes whose
    periods were created lazily at different times — a normal occurrence."""
    from uuid import uuid4

    async def _insert() -> str:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            period_id = uuid4()
            await conn.execute(
                """
                INSERT INTO envelope_periods
                    (id, user_id, envelope_id, period_start, period_end, opening_balance, created_at)
                VALUES ($1, $2, $3, $4, $5, 0, now())
                """,
                period_id,
                UUID(user_id),
                UUID(envelope_id),
                period_start,
                period_end,
            )
            await conn.execute(
                """
                INSERT INTO envelope_movements
                    (id, user_id, transaction_id, envelope_period_id, amount, created_at)
                VALUES ($1, $2, NULL, $3, $4, now())
                """,
                uuid4(),
                UUID(user_id),
                period_id,
                amount,
            )
            return str(period_id)
        finally:
            await conn.close()

    return asyncio.run(_insert())


def test_force_close_early_payday_on_period_start_day(
    client: TestClient, database_url: str
) -> None:
    """Regression test: declaring an early salary on the very day the current
    period started must not fail.

    force_close_current_cycle truncates every active period to early_date. It
    selected them with period_start <= early_date, so a period beginning on
    early_date itself was truncated to period_end == period_start, which
    ck_env_period_date_range rejects - the declaration came back as a 500. This
    is reachable whenever a user declares a salary twice on the same day, which
    is exactly what the first income of a fresh account sets up.
    """
    user = create_user(client, "early-payday-same-day@example.com", sweep_days=30)
    user_id = user["id"]

    anchor_date = date(2026, 6, 1)
    set_user_anchor_date(database_url, user_id, anchor_date)
    insert_onboarding_record(database_url, user_id, {"sweep_anchor_date": "2026-06-01"})

    envelope = create_envelope(client, user_id, "Food")
    category = create_category(client, user_id, "Food Expense")
    map_category(client, user_id, category["id"], envelope["id"])
    income_category = create_category(client, user_id, "income_salary")

    # First salary: opens the period that starts on this very date.
    first = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category["id"],
            "amount": "2100.00",
            "occurred_on": "2026-06-01",
            "description": "Salary",
        },
    )
    assert first.status_code == 201

    # Second salary the same day, declared as an early payday. The period to
    # close began today, so there is no elapsed cycle to truncate.
    for shift in (False, True):
        response = client.post(
            "/transactions",
            json={
                "type": "income",
                "category_id": income_category["id"],
                "amount": "2100.00",
                "occurred_on": "2026-06-01",
                "description": "Early salary same day",
                "permanent_shift": shift,
            },
        )
        assert response.status_code == 201, (
            f"permanent_shift={shift} on the period start day returned "
            f"{response.status_code}: {response.text}"
        )


def test_force_close_sweeps_every_envelope_even_with_divergent_period_start(
    client: TestClient, database_url: str
) -> None:
    """Regression test: force_close_current_cycle + run_sweep(force=True) must
    resolve period_start per envelope. Before the fix, a single period_start was
    borrowed from an arbitrary envelope and applied to every envelope, orphaning
    the real balance of any envelope whose active period started on a different
    date (a normal situation since periods are created lazily)."""
    user = create_user(client, "force-close-divergent@example.com", sweep_days=30)
    user_id = user["id"]

    anchor_date = date(2026, 6, 1)
    set_user_anchor_date(database_url, user_id, anchor_date)
    insert_onboarding_record(database_url, user_id, {"sweep_anchor_date": "2026-06-01"})

    # Envelope A: funded through the normal flow -> period_start = 2026-06-01 (anchor-aligned).
    envelope_a = create_envelope(client, user_id, "Rent")
    category_a = create_category(client, user_id, "Rent Expense")
    map_category(client, user_id, category_a["id"], envelope_a["id"])
    client.post(
        f"/envelopes/{envelope_a['id']}/allocate",
        json={"amount": "100.00", "occurred_on": "2026-06-05"},
    )

    # Envelope B: its currently-active period was seeded earlier, with a
    # deliberately different period_start (2026-05-01) than envelope A's.
    envelope_b = create_envelope(client, user_id, "Insurance")
    divergent_period_id = insert_envelope_period_with_movement(
        database_url,
        user_id,
        envelope_b["id"],
        period_start=date(2026, 5, 1),
        period_end=date(2026, 7, 1),
        amount="250.00",
    )

    income_category = create_category(client, user_id, "income_salary")

    # Permanent payday shift on 2026-06-25 closes every active period (including
    # envelope B's, whose period_start differs from envelope A's) and sweeps them.
    response = client.post(
        "/transactions",
        json={
            "type": "income",
            "category_id": income_category["id"],
            "amount": "1000.00",
            "occurred_on": "2026-06-25",
            "description": "Payday shift",
            "permanent_shift": True,
        },
    )
    assert response.status_code == 201

    # Envelope B's real (pre-seeded) period must be the one swept, with its
    # actual balance — not lost to a freshly created zero-balance phantom period.
    swept_amount = fetch_sweep_amount(database_url, divergent_period_id)
    assert swept_amount is not None, (
        "envelope B's real period was never swept — its balance is orphaned"
    )
    assert Decimal(swept_amount) == Decimal("250.00")

    # Envelope A must still be swept correctly too (no regression there).
    sweeps_count = fetch_sweeps_count_for_date(database_url, user_id, date(2026, 6, 25))
    assert sweeps_count >= 2

