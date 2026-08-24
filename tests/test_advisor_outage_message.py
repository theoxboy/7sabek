from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.ai_gateway_client import AIGatewayQuotaError
from tests.utils import register_user

RAW_PROVIDER_ERROR = (
    "AI provider returned 402: {\"error\":{\"message\":\"This request requires more "
    "credits, or fewer max_tokens. You requested up to 16384 tokens, but can only "
    "afford 16126. To increase, visit https://openrouter.ai/settings/credits\"}}"
)


def _ask(client: TestClient) -> str:
    response = client.post(
        "/advisor/chat",
        json={"messages": [{"role": "user", "text": "Combien puis-je dépenser ?"}]},
    )
    assert response.status_code == 200
    return response.json()["text"]


def test_a_billing_failure_is_not_printed_to_the_user(client: TestClient) -> None:
    """A provider outage is not the user's problem, and not their business.

    The raw error used to be rendered straight into the chat bubble: the
    provider's name, its billing URL and the token accounting of the request.
    """
    register_user(client, "advisor-quota@example.com")

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError(RAW_PROVIDER_ERROR),
    ):
        reply = _ask(client)

    lowered = reply.lower()
    assert "openrouter" not in lowered
    assert "max_tokens" not in lowered
    assert "402" not in reply
    assert "credits" not in lowered
    # It says the chat is limited, and when it lifts - the way any metered
    # assistant does - instead of showing the provider's billing problem.
    assert "limite" in lowered
    assert "revenez après" in lowered
    assert len(reply) > 120


def test_the_limit_message_names_the_moment_to_come_back(client: TestClient) -> None:
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    register_user(client, "advisor-quota-date@example.com")

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError(RAW_PROVIDER_ERROR),
    ):
        reply = _ask(client)

    expected = (datetime.now(timezone.utc) + timedelta(hours=24)).astimezone(
        ZoneInfo("Africa/Casablanca")
    )
    months_fr = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    assert f"{expected.day} {months_fr[expected.month - 1]} {expected.year}" in reply


def test_a_provider_supplied_window_is_used_instead_of_the_default_day(client: TestClient) -> None:
    """A rate limit that carries its own window must not be rounded up to 24h."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    register_user(client, "advisor-quota-retry@example.com")

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError('AI provider returned 429: {"retry_after": 900}'),
    ):
        reply = _ask(client)

    soon = (datetime.now(timezone.utc) + timedelta(seconds=900)).astimezone(
        ZoneInfo("Africa/Casablanca")
    )
    assert soon.strftime("%H:%M")[:4] in reply or soon.strftime("%H:%M") in reply


def test_superadmin_still_sees_the_diagnostic(client: TestClient, database_url: str) -> None:
    import asyncio

    import asyncpg

    register_user(client, "advisor-admin@example.com")

    async def _promote() -> None:
        url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(
                "UPDATE users SET role = 'superadmin' WHERE email = $1",
                "advisor-admin@example.com",
            )
        finally:
            await conn.close()

    asyncio.run(_promote())

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError(RAW_PROVIDER_ERROR),
    ):
        reply = _ask(client)

    assert "diagnostic superadmin" in reply.lower()


def test_the_exchange_is_still_recorded_when_the_provider_fails(client: TestClient) -> None:
    register_user(client, "advisor-quota-history@example.com")

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError(RAW_PROVIDER_ERROR),
    ):
        _ask(client)

    history = client.get("/advisor/chat/history").json()
    assert [item["role"] for item in history] == ["user", "assistant"]


def test_the_deadline_does_not_move_when_the_user_tries_again(client: TestClient) -> None:
    """The window is anchored on the first refusal.

    Recomputing "now + 24h" on every attempt pushed the deadline forward each
    time the user pressed send, which is not a limit but a moving target.
    """
    import re

    register_user(client, "advisor-quota-stable@example.com")

    with patch(
        "app.api.routes.advisor.chat_completion_via_gateway",
        side_effect=AIGatewayQuotaError(RAW_PROVIDER_ERROR),
    ):
        first = _ask(client)
        second = _ask(client)

    def deadline(text: str) -> str:
        match = re.search(r"\*\*(.+?)\*\*", text)
        assert match, text
        return match.group(1)

    assert deadline(first) == deadline(second)
