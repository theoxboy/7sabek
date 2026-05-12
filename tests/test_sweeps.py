from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Optional

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
    income_category = create_category(client, user["id"], "Auto Sweep Income")
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
    income_category = create_category(client, user["id"], "Auto Sweep Disabled Income")
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
    income_category = create_category(client, user["id"], "Backlog Income")
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
