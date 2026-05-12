from __future__ import annotations

from app.services.onboarding_v2_payload_normalization import (
    derive_canonical_primary_objective,
)


def test_guidance_mode_wins_over_other_primary_objective_signals() -> None:
    answers = {
        "E5_has_debt": "yes",
        "G0_has_goal": "yes",
        "F1_guidance_mode": "goal_growth_first",
        "F1_objectives_v1": ["debt"],
        "F1_priority_profile_v1": {
            "debt_priority": "debt_relief_fast",
            "goal_priority": "goal_postpone",
            "living_priority": "living_tight",
        },
    }

    assert derive_canonical_primary_objective(answers) == "goals"


def test_priority_profile_wins_over_bridged_objective_answers() -> None:
    answers = {
        "E5_has_debt": "yes",
        "G0_has_goal": "no",
        "F1_objectives_v1": ["savings"],
        "F1_priority_profile_v1": {
            "debt_priority": "debt_relief_fast",
            "goal_priority": "goal_start_light",
            "living_priority": "living_balance",
        },
    }

    assert derive_canonical_primary_objective(answers) == "debt"


def test_objective_answers_are_used_when_no_more_canonical_signal_exists() -> None:
    answers = {
        "F1_objectives_v1": ["spending_control"],
    }

    assert derive_canonical_primary_objective(answers) == "spending_control"


def test_multiple_objectives_collapse_to_all() -> None:
    answers = {
        "F1_objectives_v1": ["debt", "savings"],
    }

    assert derive_canonical_primary_objective(answers) == "all"
