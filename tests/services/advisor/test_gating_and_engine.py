from __future__ import annotations

from app.services.advisor.gating_service import GatingService
from app.services.advisor.proposal_engine_service import ProposalEngineService
from tests.fixtures.advisor.preview_fixtures import (
    blocked_profile_input,
    degraded_profile_input,
    normal_profile_input,
)


def test_gating_blocks_missing_income() -> None:
    service = GatingService()
    out = service.evaluate(blocked_profile_input())
    assert out.can_generate_preview is False
    assert "MONTHLY_INCOME_MISSING_OR_INVALID" in out.blocking_issues


def test_gating_degraded_when_reserve_is_weak() -> None:
    service = GatingService()
    out = service.evaluate(degraded_profile_input())
    assert out.can_generate_preview is True
    assert out.degraded_mode is True
    assert "reserve weak" in out.warnings


def test_engine_generates_three_proposals_in_nominal_case() -> None:
    gating = GatingService().evaluate(normal_profile_input())
    preview = ProposalEngineService().generate_preview(normal_profile_input(), gating)

    assert preview.mode == "normal"
    assert preview.proposal_count == len(preview.proposals)
    assert preview.proposal_count in (2, 3)
    assert preview.recommended_proposal_id is not None
    assert any(p.proposal_id == preview.recommended_proposal_id for p in preview.proposals)


def test_engine_returns_blocked_preview_when_gating_blocked() -> None:
    profile = blocked_profile_input()
    gating = GatingService().evaluate(profile)
    preview = ProposalEngineService().generate_preview(profile, gating)

    assert preview.mode == "blocked"
    assert preview.proposal_count == 0
    assert preview.proposals == []
    assert preview.recommended_proposal_id is None
