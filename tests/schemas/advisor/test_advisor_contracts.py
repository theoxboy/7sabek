from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.advisor.api import PostAdvisorPreviewResponse
from app.schemas.advisor.contracts import NormalizedFinancialProfileV1


def _valid_preview_payload() -> dict:
    preview_id = str(uuid4())
    proposal_id = "balanced-1"
    return {
        "preview_id": preview_id,
        "advisor_preview": {
            "preview_id": preview_id,
            "engine_version": "advisor-engine-v1",
            "generated_at": "2026-04-03T12:00:00Z",
            "mode": "normal",
            "degraded_mode": False,
            "can_recommend_confidently": True,
            "recommended_proposal_id": proposal_id,
            "recommendation_reason_tags": ["stable_income"],
            "warnings": [],
            "blocking_issues": [],
            "missing_required_fields": [],
            "data_quality_summary": {
                "completeness_score": 92,
                "reliability_score": 88,
            },
            "proposal_count": 1,
            "proposals": [
                {
                    "proposal_id": proposal_id,
                    "proposal_type": "balanced",
                    "rank": 1,
                    "is_recommended": True,
                    "title_key": "advisor.proposal.balanced.title",
                    "subtitle_key": "advisor.proposal.balanced.subtitle",
                    "fit_profile_tags": ["stable"],
                    "allocation": {
                        "period_basis": {
                            "cycle_days": 30,
                            "monthly_reference_amount": 10000,
                            "cycle_reference_amount": 10000,
                        },
                        "monthly": {
                            "essentials": 4000,
                            "debt_minimums": 500,
                            "debt_extra": 200,
                            "reserve": 400,
                            "sinking_funds": 300,
                            "goals": 1200,
                            "flexible": 800,
                            "total_allocated": 7400,
                            "unallocated_buffer": 2600,
                        },
                        "cycle": {
                            "essentials": 4000,
                            "debt_minimums": 500,
                            "debt_extra": 200,
                            "reserve": 400,
                            "sinking_funds": 300,
                            "goals": 1200,
                            "flexible": 800,
                            "total_allocated": 7400,
                            "unallocated_buffer": 2600,
                        },
                        "integrity_checks": {
                            "no_negative_allocations": True,
                            "allocation_sum_valid": True,
                            "minimum_obligations_covered": True,
                            "month_cycle_consistent": True,
                        },
                    },
                    "impact_summary": {
                        "monthly_remaining_after_plan": 2600,
                        "cycle_remaining_after_plan": 2600,
                        "debt_coverage_ratio": 1.0,
                        "reserve_progress_ratio": 0.4,
                        "goals_funding_ratio": 0.3,
                        "sinking_coverage_ratio": 0.2,
                    },
                    "tradeoffs": {
                        "pros_tags": ["balanced"],
                        "cons_tags": ["slower_debt"],
                        "tradeoff_tags": ["moderate_goal_speed"],
                    },
                    "proposal_warnings": [],
                    "risk_signals": {
                        "risk_level": "low",
                        "risk_tags": ["low_risk"],
                    },
                    "deltas_vs_recommended": {
                        "monthly_debt_extra_delta": 0,
                        "monthly_goals_delta": 0,
                        "monthly_reserve_delta": 0,
                        "monthly_flexible_delta": 0,
                        "monthly_sinking_delta": 0,
                    },
                    "recommendation_layer": {
                        "main_priority": "stability",
                        "reason_tags": ["stable_income"],
                        "tradeoff_tags": ["moderate_goal_speed"],
                        "recommended_for_tags": ["profil stable"],
                        "risk_tags": ["low_risk"],
                    },
                    "review_details": {
                        "what_is_protected": ["essentials"],
                        "what_is_limited": ["flexible"],
                        "what_may_be_delayed": ["goal_extra"],
                        "assumptions_used": ["monthly_baseline"],
                    },
                }
            ],
            "comparison_summary": {
                "primary_axis": "stability",
                "best_for_stability": proposal_id,
                "best_for_debt_speed": proposal_id,
                "best_for_goal_progress": proposal_id,
                "best_for_cash_safety": proposal_id,
            },
            "apply_preview_summary": {
                "proposal_id": proposal_id,
                "envelopes_impact": {
                    "create_count": 1,
                    "update_count": 3,
                    "freeze_count": 0,
                },
                "goals_impact": {
                    "active_count": 1,
                    "slowed_count": 0,
                    "paused_count": 0,
                },
                "rules_impact": {
                    "create_count": 1,
                    "update_count": 2,
                    "disable_count": 0,
                },
                "reserve_impact": {
                    "monthly_contribution": 400,
                    "cycle_contribution": 400,
                    "starter_gap_after_apply": 600,
                },
                "debt_strategy_impact": {
                    "minimums_covered": True,
                    "focus_enabled": True,
                    "target_debt_id": "debt-1",
                    "monthly_extra_amount": 200,
                },
                "safety": {
                    "requires_user_confirmation": True,
                    "apply_allowed_if_confirmed": True,
                },
            },
        },
        "freshness": {
            "profile_hash": "profile-hash-1",
            "engine_version": "advisor-engine-v1",
            "generated_at": "2026-04-03T12:00:00Z",
            "expires_at": "2026-04-03T12:30:00Z",
        },
    }


def test_valid_preview_payload_is_accepted() -> None:
    payload = _valid_preview_payload()
    parsed = PostAdvisorPreviewResponse.model_validate(payload)
    assert parsed.advisor_preview.proposal_count == 1
    assert parsed.advisor_preview.recommended_proposal_id == "balanced-1"


def test_missing_required_field_is_rejected() -> None:
    payload = _valid_preview_payload()
    payload.pop("advisor_preview")
    with pytest.raises(ValidationError):
        PostAdvisorPreviewResponse.model_validate(payload)


def test_invalid_enum_value_is_rejected() -> None:
    payload = _valid_preview_payload()
    payload["advisor_preview"]["proposals"][0]["proposal_type"] = "not_a_type"
    with pytest.raises(ValidationError):
        PostAdvisorPreviewResponse.model_validate(payload)


def test_negative_numeric_value_is_rejected() -> None:
    profile_payload = {
        "metadata": {
            "schema_version": "NormalizedFinancialProfileV1",
            "profile_id": str(uuid4()),
            "user_id": str(uuid4()),
            "generated_at": "2026-04-03T12:00:00Z",
            "source_context": "onboarding_v2",
            "currency": "MAD",
            "cycle_days": 30,
        },
        "income_profile": {
            "monthly_income_total": -10,
            "cycle_income_total": 0,
            "income_streams": [],
        },
        "expense_profile": {
            "monthly_essential_total": 0,
            "monthly_expense_total_all": 0,
            "monthly_sinking_obligations_total": 0,
            "expenses": [],
        },
        "debt_profile": {
            "has_debt": False,
            "monthly_debt_minimum_total": 0,
            "debts": [],
        },
        "goals_profile": {
            "goals_count": 0,
            "goals_target_total": 0,
            "goals_started_count": 0,
            "goals_with_target_date_count": 0,
            "goals": [],
        },
        "reserve_profile": {
            "reserve_current_amount": 0,
            "reserve_target_starter": 0,
            "reserve_gap_to_starter": 0,
        },
        "current_cash_snapshot": {
            "available_now_amount": 0,
        },
    }
    with pytest.raises(ValidationError):
        NormalizedFinancialProfileV1.model_validate(profile_payload)


def test_extra_field_is_rejected() -> None:
    payload = _valid_preview_payload()
    payload["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        PostAdvisorPreviewResponse.model_validate(payload)


def test_requires_user_confirmation_must_be_true() -> None:
    payload = _valid_preview_payload()
    payload = deepcopy(payload)
    payload["advisor_preview"]["apply_preview_summary"]["safety"]["requires_user_confirmation"] = False
    with pytest.raises(ValidationError):
        PostAdvisorPreviewResponse.model_validate(payload)
