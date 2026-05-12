from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.schemas.advisor.api import PostAdvisorPreviewResponse
from app.services.advisor.preview_service import AdvisorPreviewService
from tests.fixtures.advisor.preview_fixtures import (
    blocked_preview_output,
    degraded_preview_output,
    normal_preview_output,
)
from tests.utils import login_user, register_user



def _current_user_id(client: TestClient) -> UUID:
    me = client.get("/auth/me")
    assert me.status_code == 200
    return UUID(me.json()["id"])



def _payload_for(client: TestClient, force_regenerate: bool = False) -> dict:
    return {
        "user_id": str(_current_user_id(client)),
        "source": "onboarding",
        "force_regenerate": force_regenerate,
    }



def _envelope(preview):
    now = datetime.now(timezone.utc)
    return PostAdvisorPreviewResponse(
        preview_id=preview.preview_id,
        advisor_preview=preview,
        freshness={
            "profile_hash": "hash-contract",
            "engine_version": preview.engine_version,
            "generated_at": now,
            "expires_at": now + timedelta(minutes=30),
        },
    )



def test_preview_returns_valid_normal_response(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.normal@example.com")
    login_user(client, "advisor.contract.normal@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        return _envelope(normal_preview_output())

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 200

    parsed = PostAdvisorPreviewResponse.model_validate(response.json())
    assert parsed.advisor_preview.mode == "normal"
    assert parsed.advisor_preview.proposal_count == len(parsed.advisor_preview.proposals)
    if parsed.advisor_preview.recommended_proposal_id is not None:
        assert any(p.proposal_id == parsed.advisor_preview.recommended_proposal_id for p in parsed.advisor_preview.proposals)



def test_preview_returns_valid_blocked_response(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.blocked@example.com")
    login_user(client, "advisor.contract.blocked@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        return _envelope(blocked_preview_output())

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 200
    payload = response.json()["advisor_preview"]

    assert payload["mode"] == "blocked"
    assert payload["proposals"] == []
    assert payload["proposal_count"] == 0
    assert payload["recommended_proposal_id"] is None



def test_preview_returns_valid_degraded_response(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.degraded@example.com")
    login_user(client, "advisor.contract.degraded@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        return _envelope(degraded_preview_output())

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 200
    payload = response.json()["advisor_preview"]

    assert payload["degraded_mode"] is True
    assert len(payload["warnings"]) > 0
    assert payload["proposal_count"] > 0



def test_preview_returns_422_on_normalizer_failure(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.422@example.com")
    login_user(client, "advisor.contract.422@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        raise RuntimeError("ADVISOR_NORMALIZER_FAILED")

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 422
    assert response.json()["detail"] == "ADVISOR_NORMALIZER_FAILED"



def test_preview_returns_500_on_persistence_failure(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.persist@example.com")
    login_user(client, "advisor.contract.persist@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        raise RuntimeError("ADVISOR_PREVIEW_PERSIST_FAILED")

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 500
    assert response.json()["detail"] == "ADVISOR_PREVIEW_PERSIST_FAILED"



def test_preview_returns_500_on_internal_error(client: TestClient, monkeypatch) -> None:
    register_user(client, "advisor.contract.500@example.com")
    login_user(client, "advisor.contract.500@example.com")

    async def _fake_generate(self, db, user, source, force_regenerate=False):
        raise Exception("boom")

    monkeypatch.setattr(AdvisorPreviewService, "generate", _fake_generate)

    response = client.post("/advisor/preview", json=_payload_for(client))
    assert response.status_code == 500
    assert response.json()["detail"] == "INTERNAL_ERROR"
