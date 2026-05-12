from __future__ import annotations

from app.services.onboarding_v2_payload_normalization import (
    normalize_onboarding_progress_snapshot,
)


def test_fallback_does_not_jump_to_smart_settings_from_partial_signals() -> None:
    progress = normalize_onboarding_progress_snapshot(
        None,
        answers={
            "SWP1_last_income_date": "2026-04-20",
        },
        draft_objects={},
        stored_workflow_stage="in_progress",
    )

    assert progress is None


def test_fallback_keeps_smart_settings_for_review_stage() -> None:
    progress = normalize_onboarding_progress_snapshot(
        None,
        answers={
            "SWP1_last_income_date": "2026-04-20",
        },
        draft_objects={},
        stored_workflow_stage="review",
    )

    assert progress is not None
    assert progress["step_id"] == "E12_smart_settings"
    assert progress["journey_mode"] == "money_plan"


def test_fallback_jumps_to_smart_settings_when_distribution_done() -> None:
    progress = normalize_onboarding_progress_snapshot(
        None,
        answers={
            "E11b_distribution_setup": "done",
        },
        draft_objects={},
        stored_workflow_stage="in_progress",
    )

    assert progress is not None
    assert progress["step_id"] == "E12_smart_settings"
    assert progress["journey_mode"] == "money_plan"


def test_legacy_completion_subview_defaults_to_onboarding_without_money_plan_context() -> None:
    progress = normalize_onboarding_progress_snapshot(
        {
            "step_id": "E5_has_debt",
            "subview": "completion",
        },
        answers={},
        draft_objects={},
        stored_workflow_stage="in_progress",
    )

    assert progress is not None
    assert progress["journey_mode"] == "onboarding"
    assert progress["subview"] == "journey_ready"
    assert progress["step_id"] == "E5_has_debt"


def test_legacy_completion_subview_keeps_money_plan_with_explicit_context() -> None:
    progress = normalize_onboarding_progress_snapshot(
        {
            "step_id": "E12_smart_settings",
            "subview": "completion",
            "review_context": {"screen": "money_plan"},
        },
        answers={},
        draft_objects={},
        stored_workflow_stage="review",
    )

    assert progress is not None
    assert progress["journey_mode"] == "money_plan"
    assert progress["step_id"] == "E12_smart_settings"
