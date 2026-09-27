from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.api.routes.analytics import _GUEST_EVENT_NAMES
from app.api.routes.auth import claim_guest_with_passkey
from app.models.user import User


def test_guest_event_names_include_telemetry_events() -> None:
    assert "guest_first_envelope" in _GUEST_EVENT_NAMES
    assert "guest_post_ack_prompt_shown" in _GUEST_EVENT_NAMES
    assert "guest_post_ack_prompt_converted" in _GUEST_EVENT_NAMES
    assert "guest_post_ack_prompt_dismissed" in _GUEST_EVENT_NAMES
    print("✓ test_guest_event_names_include_telemetry_events PASSED")


async def test_claim_guest_with_passkey_blocks_when_registration_disabled() -> None:
    mock_db = AsyncMock()
    mock_request = MagicMock()
    mock_response = MagicMock()
    guest_user = User(is_guest=True, id=MagicMock())

    # Mock platform settings with registration_enabled = False
    mock_settings = MagicMock()
    mock_settings.registration_enabled = False

    with patch("app.api.routes.auth.enforce_rate_limit", new_callable=AsyncMock), \
         patch("app.api.routes.auth.get_platform_settings", new_callable=AsyncMock, return_value=mock_settings):
        try:
            await claim_guest_with_passkey(
                request=mock_request,
                response=mock_response,
                db=mock_db,
                user=guest_user,
            )
            assert False, "Should have raised HTTPException 403"
        except HTTPException as exc:
            assert exc.status_code == 403, f"Expected 403, got {exc.status_code}"
            assert "fermées" in exc.detail
            print("✓ test_claim_guest_with_passkey_blocks_when_registration_disabled PASSED")


def main():
    test_guest_event_names_include_telemetry_events()
    asyncio.run(test_claim_guest_with_passkey_blocks_when_registration_disabled())
    print("\nALL AUDIT FIX TESTS (A1-A5) PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
