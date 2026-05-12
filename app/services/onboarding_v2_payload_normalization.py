from __future__ import annotations

import re
from typing import Any


MODERN_OBJECTIVES_KEY = "F1_objectives_v1"
MODERN_PRIORITY_PROFILE_KEY = "F1_priority_profile_v1"
MODERN_ENVELOPE_PREFERENCES_KEY = "E11_envelope_preferences_v1"
CANONICAL_PRIMARY_OBJECTIVES = {
    "spending_control",
    "savings",
    "debt",
    "goals",
    "all",
}
GUIDANCE_MODE_TO_PRIMARY_OBJECTIVE = {
    "debt_relief_first": "debt",
    "stability_first": "savings",
    "goal_growth_first": "goals",
    "balanced_rebuild": "all",
}

LEGACY_OBJECTIVES_KEY = "Q0b_primary_objective"
LEGACY_PRIORITY_PROFILE_KEYS = (
    "P1_priority_profile",
    "P1_debt_priority",
    "P1_goal_priority",
    "P1_living_priority",
)
LEGACY_ENVELOPE_PREFERENCE_KEYS = (
    "E7_lifestyle",
    "E8_envelope_granularity",
    "E10_keep_suggestions",
)

MONEY_PLAN_QUESTION_IDS = {
    "F0_financial_summary",
    "F1_interactive_guidance",
    "E11_envelope_setup",
    "E11b_distribution_setup",
    "E12_smart_settings",
}
LEGACY_MONEY_PLAN_SUMMARY_QUESTION_IDS = {
    "D2_debt_preferences",
    "D3_debt_summary",
    "G2_goal_preferences",
}
LEGACY_MONEY_PLAN_ENVELOPE_QUESTION_IDS = {
    "Q0a_envelope_bridge_message",
    "Q0b_primary_objective",
    "Q0c_objective_intro_message",
    "E7_lifestyle",
    "E8_envelope_granularity",
    "E10_keep_suggestions",
}
VALID_PROGRESS_SUBVIEWS = {
    "question",
    "journey_ready",
    "financial_review",
    "expense_review",
    "distribution_review",
}
MONEY_PLAN_REVIEW_STEP_ID = "E12_smart_settings"
LEGACY_FINAL_REVIEW_SUBVIEWS = {
    "ready",
    "rollover_config",
    "sweep_setup",
    "completion",
}


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _safe_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        return list(dict.fromkeys(items))
    single = _safe_string(value)
    return [single] if single else []


def _safe_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _normalize_review_context(
    explicit: dict[str, Any] | None,
    *,
    subview: str | None,
    journey_mode: str | None,
) -> dict[str, Any] | None:
    canonical_screen: str | None = None
    if subview in {"financial_review", "expense_review"}:
        canonical_screen = "financial_summary"
    elif subview == "journey_ready":
        canonical_screen = "journey_ready"
    elif subview == "distribution_review":
        canonical_screen = (
            "distribution_review" if journey_mode == "money_plan" else "journey_ready"
        )

    if explicit is not None:
        next_value = dict(explicit)
        if canonical_screen:
            next_value["screen"] = canonical_screen
        elif next_value.get("screen") in {
            "money_plan",
            "distribution_review",
            "journey_ready",
        } or not isinstance(next_value.get("screen"), str):
            next_value.pop("screen", None)
        return next_value or None

    if canonical_screen:
        return {"screen": canonical_screen}
    return None


def _get_priority_profile_dict(answers: dict[str, Any]) -> dict[str, Any]:
    raw = _safe_dict(answers.get(MODERN_PRIORITY_PROFILE_KEY)) or {}
    return {
        key: value
        for key, value in raw.items()
        if key in {"debt_priority", "goal_priority", "living_priority"}
        and isinstance(value, str)
        and value.strip()
    }


def _get_envelope_preferences_dict(answers: dict[str, Any]) -> dict[str, Any]:
    raw = _safe_dict(answers.get(MODERN_ENVELOPE_PREFERENCES_KEY)) or {}
    next_value: dict[str, Any] = {}
    lifestyle_margin_level = _safe_string(raw.get("lifestyle_margin_level"))
    if lifestyle_margin_level:
        next_value["lifestyle_margin_level"] = lifestyle_margin_level
    selected_suggestion_slugs = _safe_string_list(raw.get("selected_suggestion_slugs"))
    if selected_suggestion_slugs:
        next_value["selected_suggestion_slugs"] = selected_suggestion_slugs
    return next_value


def _has_guidance_signals(answers: dict[str, Any]) -> bool:
    return _safe_string(answers.get("F1_guidance_mode")) is not None


def _has_envelope_setup_signals(
    answers: dict[str, Any],
    draft_objects: dict[str, Any] | None,
) -> bool:
    if get_onboarding_answer_string(answers, "E7_lifestyle"):
        return True
    if get_onboarding_answer_list(answers, "E10_keep_suggestions"):
        return True
    proposal = _safe_dict((draft_objects or {}).get("envelopes_proposal_v1"))
    if proposal is None:
        return False
    return any(
        isinstance(proposal.get(key), list)
        for key in ("selected_envelopes", "candidates", "excluded_envelopes")
    )


def _has_distribution_setup_signals(answers: dict[str, Any]) -> bool:
    return get_onboarding_answer_string(answers, "E11b_distribution_setup") == "done"


def _has_any_smart_settings_signals(answers: dict[str, Any]) -> bool:
    date = get_onboarding_answer_string(answers, "SWP1_last_income_date")
    amount_raw = get_onboarding_answer_string(answers, "SWP2_last_income_amount")
    amount = _safe_number(amount_raw)
    return bool(date) or amount > 0


def _is_smart_settings_complete(answers: dict[str, Any]) -> bool:
    date = get_onboarding_answer_string(answers, "SWP1_last_income_date")
    amount_raw = get_onboarding_answer_string(answers, "SWP2_last_income_amount")
    amount = _safe_number(amount_raw)
    return bool(date) and amount > 0


def _safe_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    raw = value.strip().replace(" ", "")
    if not raw:
        return 0.0
    has_dot = "." in raw
    has_comma = "," in raw
    cleaned = raw
    if has_dot and has_comma:
        last_dot = raw.rfind(".")
        last_comma = raw.rfind(",")
        decimal_sep = "." if last_dot > last_comma else ","
        thousands_sep = "," if decimal_sep == "." else "."
        cleaned = raw.replace(thousands_sep, "")
        if decimal_sep == ",":
            cleaned = cleaned.replace(",", ".")
    elif has_dot:
        if re.match(r"^-?\d{1,3}(\.\d{3})+([.,]\d+)?$", raw):
            cleaned = raw.replace(".", "")
    elif has_comma:
        if re.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$", raw):
            cleaned = raw.replace(",", "")
        else:
            cleaned = raw.replace(",", ".")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return 0.0


def _can_use_money_plan_distribution_review(
    *,
    journey_mode: str | None,
    step_id: str | None,
    answers: dict[str, Any],
) -> bool:
    return (
        journey_mode == "money_plan"
        and step_id == MONEY_PLAN_REVIEW_STEP_ID
        and _is_smart_settings_complete(answers)
    )


def _resolve_fallback_money_plan_step_id(
    answers: dict[str, Any],
    draft_objects: dict[str, Any] | None,
    stored_workflow_stage: str | None,
) -> str | None:
    if stored_workflow_stage == "review" and (
        _has_any_smart_settings_signals(answers)
        or _has_distribution_setup_signals(answers)
        or _has_envelope_setup_signals(answers, draft_objects)
    ):
        return MONEY_PLAN_REVIEW_STEP_ID
    if _has_distribution_setup_signals(answers):
        return MONEY_PLAN_REVIEW_STEP_ID
    if _has_envelope_setup_signals(answers, draft_objects):
        # Envelope setup signals indicate the user has reached money-plan setup.
        # When progress snapshot is missing, resume from smart settings entrypoint.
        return MONEY_PLAN_REVIEW_STEP_ID
    if _has_guidance_signals(answers):
        return "F1_interactive_guidance"
    return None


def _build_fallback_progress_snapshot(
    *,
    answers: dict[str, Any],
    draft_objects: dict[str, Any] | None,
    stored_workflow_stage: str | None,
) -> dict[str, Any] | None:
    step_id = _resolve_fallback_money_plan_step_id(
        answers, draft_objects, stored_workflow_stage
    )
    if not step_id:
        return None
    step_index_by_id = {
        "F0_financial_summary": 0,
        "F1_interactive_guidance": 1,
        "E11_envelope_setup": 2,
        "E11b_distribution_setup": 3,
        MONEY_PLAN_REVIEW_STEP_ID: 4,
    }
    subview = (
        "distribution_review"
        if step_id == MONEY_PLAN_REVIEW_STEP_ID and _is_smart_settings_complete(answers)
        else "question"
    )
    return {
        "flow_stage": "questions",
        "step_index": step_index_by_id.get(step_id, 0),
        "current_question_id": step_id,
        "journey_mode": "money_plan",
        "step_id": step_id,
        "subview": subview,
        "modal_state": None,
        "review_context": _normalize_review_context(
            None,
            subview=subview,
            journey_mode="money_plan",
        ),
    }


def get_onboarding_answer_string(answers: dict[str, Any], key: str) -> str:
    if key == LEGACY_OBJECTIVES_KEY:
        values = get_onboarding_answer_list(answers, key)
        return values[0] if values else ""
    if key == "P1_debt_priority":
        modern = _safe_string(_get_priority_profile_dict(answers).get("debt_priority"))
        return modern or (_safe_string(answers.get(key)) or "")
    if key == "P1_goal_priority":
        modern = _safe_string(_get_priority_profile_dict(answers).get("goal_priority"))
        return modern or (_safe_string(answers.get(key)) or "")
    if key == "P1_living_priority":
        modern = _safe_string(_get_priority_profile_dict(answers).get("living_priority"))
        return modern or (_safe_string(answers.get(key)) or "")
    if key == "E7_lifestyle":
        modern = _safe_string(
            _get_envelope_preferences_dict(answers).get("lifestyle_margin_level")
        )
        return modern or (_safe_string(answers.get(key)) or "")
    value = answers.get(key)
    return value.strip() if isinstance(value, str) else ""


def get_onboarding_answer_list(answers: dict[str, Any], key: str) -> list[str]:
    if key == LEGACY_OBJECTIVES_KEY:
        modern = _safe_string_list(answers.get(MODERN_OBJECTIVES_KEY))
        if modern:
            return modern
        return _safe_string_list(answers.get(key))
    if key == "E10_keep_suggestions":
        modern = _safe_string_list(
            _get_envelope_preferences_dict(answers).get("selected_suggestion_slugs")
        )
        if modern:
            return modern
        return _safe_string_list(answers.get(key))
    return _safe_string_list(answers.get(key))


def _derive_primary_objective_from_guidance_mode(
    answers: dict[str, Any],
) -> str | None:
    guidance_mode = _safe_string(answers.get("F1_guidance_mode"))
    if guidance_mode in GUIDANCE_MODE_TO_PRIMARY_OBJECTIVE:
        return GUIDANCE_MODE_TO_PRIMARY_OBJECTIVE[guidance_mode]
    return None


def _derive_primary_objective_from_priority_profile(
    answers: dict[str, Any],
) -> str | None:
    priority_profile = _get_priority_profile_dict(answers)
    if not priority_profile:
        return None
    debt_choice = _safe_string(priority_profile.get("debt_priority"))
    goal_choice = _safe_string(priority_profile.get("goal_priority"))
    living_choice = _safe_string(priority_profile.get("living_priority"))
    if debt_choice == "debt_relief_fast":
        return "debt"
    if goal_choice == "goal_start_now":
        return "goals"
    if living_choice == "living_comfort":
        return "savings"
    return "all"


def _derive_primary_objective_from_objective_answers(
    answers: dict[str, Any],
) -> str | None:
    values = [
        value
        for value in _safe_string_list(answers.get(MODERN_OBJECTIVES_KEY))
        if value in CANONICAL_PRIMARY_OBJECTIVES
    ]
    if not values:
        return None
    non_all_values = [value for value in values if value != "all"]
    if len(non_all_values) == 1:
        return non_all_values[0]
    if "all" in values or len(non_all_values) > 1:
        return "all"
    return "all"


def derive_canonical_primary_objective(answers: dict[str, Any]) -> str | None:
    normalized_answers = normalize_onboarding_answers(answers)
    # Priority is explicit and stable:
    # 1. interactive guidance preset selected by the user
    # 2. modern priority profile choices (P1_* normalized into F1_priority_profile_v1)
    # 3. objective list persisted in answers, which may be bridged for compatibility
    return (
        _derive_primary_objective_from_guidance_mode(normalized_answers)
        or _derive_primary_objective_from_priority_profile(normalized_answers)
        or _derive_primary_objective_from_objective_answers(normalized_answers)
    )


def extract_primary_objective(answers: dict[str, Any]) -> str | None:
    return derive_canonical_primary_objective(answers)


def normalize_onboarding_answers(answers: Any) -> dict[str, Any]:
    next_answers = dict(answers) if isinstance(answers, dict) else {}

    objective_values = get_onboarding_answer_list(next_answers, LEGACY_OBJECTIVES_KEY)
    if objective_values:
        next_answers[MODERN_OBJECTIVES_KEY] = objective_values
    else:
        next_answers.pop(MODERN_OBJECTIVES_KEY, None)

    priority_profile: dict[str, Any] = {}
    debt_priority = get_onboarding_answer_string(next_answers, "P1_debt_priority")
    goal_priority = get_onboarding_answer_string(next_answers, "P1_goal_priority")
    living_priority = get_onboarding_answer_string(next_answers, "P1_living_priority")
    if debt_priority:
        priority_profile["debt_priority"] = debt_priority
    if goal_priority:
        priority_profile["goal_priority"] = goal_priority
    if living_priority:
        priority_profile["living_priority"] = living_priority
    if priority_profile:
        next_answers[MODERN_PRIORITY_PROFILE_KEY] = priority_profile
    else:
        next_answers.pop(MODERN_PRIORITY_PROFILE_KEY, None)

    envelope_preferences: dict[str, Any] = {}
    lifestyle_margin_level = get_onboarding_answer_string(next_answers, "E7_lifestyle")
    selected_suggestion_slugs = get_onboarding_answer_list(
        next_answers, "E10_keep_suggestions"
    )
    if lifestyle_margin_level:
        envelope_preferences["lifestyle_margin_level"] = lifestyle_margin_level
    if selected_suggestion_slugs:
        envelope_preferences["selected_suggestion_slugs"] = selected_suggestion_slugs
    if envelope_preferences:
        next_answers[MODERN_ENVELOPE_PREFERENCES_KEY] = envelope_preferences
    else:
        next_answers.pop(MODERN_ENVELOPE_PREFERENCES_KEY, None)

    next_answers.pop(LEGACY_OBJECTIVES_KEY, None)
    for key in LEGACY_PRIORITY_PROFILE_KEYS:
        next_answers.pop(key, None)
    for key in LEGACY_ENVELOPE_PREFERENCE_KEYS:
        next_answers.pop(key, None)

    return next_answers


def _infer_progress_journey_mode(
    *,
    current_question_id: str | None,
    step_id: str | None,
    raw_subview: str | None,
    raw_progress: dict[str, Any],
) -> str | None:
    explicit = _safe_string(raw_progress.get("journey_mode"))
    if explicit in {"onboarding", "money_plan"}:
        return explicit

    candidate = step_id or current_question_id
    if candidate in MONEY_PLAN_QUESTION_IDS:
        return "money_plan"
    if candidate in LEGACY_MONEY_PLAN_SUMMARY_QUESTION_IDS or candidate in LEGACY_MONEY_PLAN_ENVELOPE_QUESTION_IDS:
        return "money_plan"
    review_context = _safe_dict(raw_progress.get("review_context")) or {}
    review_screen = _safe_string(review_context.get("screen"))
    has_legacy_final_subview = raw_subview in LEGACY_FINAL_REVIEW_SUBVIEWS
    can_map_legacy_final_to_money_plan = (
        has_legacy_final_subview
        and (
            candidate == MONEY_PLAN_REVIEW_STEP_ID
            or review_screen in {"distribution_review", "money_plan"}
        )
    )
    if (
        raw_subview == "distribution_review"
        or can_map_legacy_final_to_money_plan
        or review_screen in {"distribution_review", "money_plan"}
    ):
        return "money_plan"
    if (
        raw_subview in {"journey_ready", "ready"}
        or has_legacy_final_subview
        or review_screen == "journey_ready"
    ):
        return "onboarding"
    return "onboarding"


def _read_raw_progress_subview(
    raw_progress: dict[str, Any],
) -> str | None:
    explicit = _safe_string(raw_progress.get("subview"))
    if explicit in VALID_PROGRESS_SUBVIEWS or explicit in LEGACY_FINAL_REVIEW_SUBVIEWS:
        return explicit
    if raw_progress.get("is_completion_screen") is True:
        return "completion"
    if raw_progress.get("is_sweep_setup_screen") is True:
        return "sweep_setup"
    if raw_progress.get("is_rollover_config_screen") is True:
        return "rollover_config"
    if raw_progress.get("is_expense_review_screen") is True:
        return "expense_review"
    if raw_progress.get("is_financial_review_screen") is True:
        return "financial_review"
    if raw_progress.get("is_ready_screen") is True:
        return "ready"
    return None


def _normalize_progress_step_id(
    *,
    current_question_id: str | None,
    step_id: str | None,
    journey_mode: str | None,
    raw_subview: str | None,
) -> str | None:
    candidate = step_id or current_question_id
    if journey_mode == "money_plan" and (
        candidate == MONEY_PLAN_REVIEW_STEP_ID
        or raw_subview == "distribution_review"
        or raw_subview in LEGACY_FINAL_REVIEW_SUBVIEWS
    ):
        return MONEY_PLAN_REVIEW_STEP_ID
    return candidate


def _normalize_progress_subview(
    *,
    journey_mode: str | None,
    step_id: str | None,
    raw_subview: str | None,
    answers: dict[str, Any],
) -> str | None:
    if raw_subview == "question":
        if _can_use_money_plan_distribution_review(
            journey_mode=journey_mode,
            step_id=step_id,
            answers=answers,
        ):
            return "distribution_review"
        if step_id:
            return "question"
        return None
    if raw_subview in {"financial_review", "expense_review"}:
        return raw_subview
    if raw_subview == "journey_ready":
        return "journey_ready"
    if raw_subview == "distribution_review":
        if journey_mode == "money_plan" and step_id == MONEY_PLAN_REVIEW_STEP_ID:
            return (
                "distribution_review"
                if _can_use_money_plan_distribution_review(
                    journey_mode=journey_mode,
                    step_id=step_id,
                    answers=answers,
                )
                else "question"
            )
        return "distribution_review"
    if (
        raw_subview is None
        and _can_use_money_plan_distribution_review(
            journey_mode=journey_mode,
            step_id=step_id,
            answers=answers,
        )
    ):
        return "distribution_review"
    if raw_subview == "ready":
        if journey_mode == "money_plan":
            return (
                "distribution_review"
                if _can_use_money_plan_distribution_review(
                    journey_mode=journey_mode,
                    step_id=step_id,
                    answers=answers,
                )
                else "question"
            )
        return "journey_ready"
    if raw_subview in {"rollover_config", "sweep_setup", "completion"}:
        if journey_mode == "money_plan":
            return (
                "distribution_review"
                if _can_use_money_plan_distribution_review(
                    journey_mode=journey_mode,
                    step_id=step_id,
                    answers=answers,
                )
                else "question"
            )
        return "journey_ready"
    if step_id:
        return "question"
    return None


def _infer_progress_review_context(
    *,
    subview: str | None,
    journey_mode: str | None,
    raw_progress: dict[str, Any],
) -> dict[str, Any] | None:
    explicit = _safe_dict(raw_progress.get("review_context"))
    return _normalize_review_context(
        explicit,
        subview=subview,
        journey_mode=journey_mode,
    )


def normalize_onboarding_progress_snapshot(
    value: Any,
    *,
    answers: dict[str, Any] | None = None,
    draft_objects: dict[str, Any] | None = None,
    stored_workflow_stage: str | None = None,
) -> dict[str, Any] | None:
    raw_progress = _safe_dict(value)
    normalized_answers = answers if isinstance(answers, dict) else {}
    if raw_progress is None:
        return _build_fallback_progress_snapshot(
            answers=normalized_answers,
            draft_objects=draft_objects,
            stored_workflow_stage=stored_workflow_stage,
        )

    flow_stage = _safe_string(raw_progress.get("flow_stage"))
    if flow_stage not in {"collect_user", "intro", "questions"}:
        flow_stage = None

    step_index = raw_progress.get("step_index")
    if not isinstance(step_index, int):
        step_index = None

    current_question_id = _safe_string(raw_progress.get("current_question_id"))
    raw_step_id = _safe_string(raw_progress.get("step_id")) or current_question_id
    raw_subview = _read_raw_progress_subview(raw_progress)
    journey_mode = _infer_progress_journey_mode(
        current_question_id=current_question_id,
        step_id=raw_step_id,
        raw_subview=raw_subview,
        raw_progress=raw_progress,
    )
    step_id = _normalize_progress_step_id(
        current_question_id=current_question_id,
        step_id=raw_step_id,
        journey_mode=journey_mode,
        raw_subview=raw_subview,
    )
    current_question_id = step_id
    subview = _normalize_progress_subview(
        journey_mode=journey_mode,
        step_id=step_id,
        raw_subview=raw_subview,
        answers=normalized_answers,
    )
    if step_id is None:
        return _build_fallback_progress_snapshot(
            answers=normalized_answers,
            draft_objects=draft_objects,
            stored_workflow_stage=stored_workflow_stage,
        )

    modal_state = raw_progress.get("modal_state")
    if modal_state is not None and not isinstance(
        modal_state, (str, bool, int, float, dict, list)
    ):
        modal_state = None

    review_context = _infer_progress_review_context(
        subview=subview,
        journey_mode=journey_mode,
        raw_progress=raw_progress,
    )

    return {
        "flow_stage": flow_stage,
        "step_index": step_index,
        "current_question_id": current_question_id,
        "journey_mode": journey_mode,
        "step_id": step_id,
        "subview": subview,
        "modal_state": modal_state,
        "review_context": review_context,
    }


def normalize_onboarding_draft_objects(
    draft_objects: Any,
    *,
    answers: dict[str, Any] | None = None,
    stored_workflow_stage: str | None = None,
) -> dict[str, Any]:
    next_draft_objects = (
        dict(draft_objects) if isinstance(draft_objects, dict) else {}
    )
    normalized_progress = normalize_onboarding_progress_snapshot(
        next_draft_objects.get("onboarding_progress_v2"),
        answers=answers,
        draft_objects=next_draft_objects,
        stored_workflow_stage=stored_workflow_stage,
    )
    if normalized_progress is not None:
        next_draft_objects["onboarding_progress_v2"] = normalized_progress
    else:
        next_draft_objects.pop("onboarding_progress_v2", None)
    return next_draft_objects
