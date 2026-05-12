from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.onboarding_v2_payload_normalization import (
    normalize_onboarding_answers,
    normalize_onboarding_draft_objects,
)


WORKFLOW_STAGES = {"in_progress", "review", "completed"}
WORKFLOW_PHASES = {"collecting", "planning", "ready_for_apply", "completed"}
VALIDATION_STAGES = {"unknown", "valid", "invalid", "warning"}
MATERIALIZATION_STAGES = {"not_applied", "partially_applied", "applied"}


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def normalize_workflow_stage(value: Any, *, default: str = "in_progress") -> str:
    normalized = _safe_string(value)
    if normalized in WORKFLOW_STAGES:
        return normalized
    return default


def normalize_workflow_phase(value: Any, *, default: str = "collecting") -> str:
    normalized = _safe_string(value)
    if normalized in WORKFLOW_PHASES:
        return normalized
    return default


def normalize_validation_stage(value: Any, *, default: str = "unknown") -> str:
    normalized = _safe_string(value)
    if normalized in VALIDATION_STAGES:
        return normalized
    return default


def normalize_materialization_stage(value: Any, *, default: str = "not_applied") -> str:
    normalized = _safe_string(value)
    if normalized in MATERIALIZATION_STAGES:
        return normalized
    return default


def _has_materialization_changes(summary: dict[str, Any]) -> bool:
    for key, value in summary.items():
        if not isinstance(key, str):
            continue
        if not (
            key.endswith("_created")
            or key.endswith("_updated")
            or key.endswith("_upserted")
        ):
            continue
        try:
            if int(value) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _derive_validation_stage(summary: dict[str, Any], *, applied: bool) -> str:
    explicit = normalize_validation_stage(summary.get("validation_stage"), default="")
    if explicit:
        return explicit
    blocking_errors = summary.get("blocking_errors")
    if isinstance(blocking_errors, list) and blocking_errors:
        return "invalid"
    if summary.get("distribution_setup_valid") is True:
        return "valid"
    if summary.get("distribution_setup_valid") is False:
        return "invalid"
    if applied:
        return "unknown"
    warnings = summary.get("validation_warnings") or summary.get("distribution_validation_warnings")
    if isinstance(warnings, list) and warnings:
        return "warning"
    return "unknown"


def _derive_materialization_stage(summary: dict[str, Any], *, applied: bool) -> str:
    explicit = normalize_materialization_stage(summary.get("materialization_stage"), default="")
    if explicit:
        return explicit
    if applied:
        return "applied"
    if _has_materialization_changes(summary):
        return "partially_applied"
    return "not_applied"


def _derive_workflow_phase_from_payload(
    payload: dict[str, Any] | None,
    *,
    stored_workflow_stage: str,
    applied: bool,
) -> str:
    if applied and stored_workflow_stage == "completed":
        return "completed"

    payload_dict = dict(payload) if isinstance(payload, dict) else {}
    answers = normalize_onboarding_answers(payload_dict.get("answers"))
    draft_objects = normalize_onboarding_draft_objects(
        payload_dict.get("draft_objects"),
        answers=answers,
        stored_workflow_stage=stored_workflow_stage,
    )
    progress = draft_objects.get("onboarding_progress_v2")
    if isinstance(progress, dict):
        journey_mode = _safe_string(progress.get("journey_mode"))
        subview = _safe_string(progress.get("subview"))
        if journey_mode == "money_plan":
            if subview == "distribution_review":
                return "ready_for_apply"
            return "planning"
        if journey_mode == "onboarding":
            if subview == "journey_ready":
                return "planning"
            return "collecting"
        if subview == "distribution_review":
            return "ready_for_apply"

    if stored_workflow_stage == "review":
        return "ready_for_apply"
    if stored_workflow_stage == "completed":
        return "completed"
    return "collecting"


def build_materialized_summary(
    summary: dict[str, Any] | None,
    *,
    stored_workflow_stage: str,
    applied: bool,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    next_summary = dict(summary) if isinstance(summary, dict) else {}
    validation_stage = _derive_validation_stage(next_summary, applied=applied)
    materialization_stage = _derive_materialization_stage(next_summary, applied=applied)
    stored_stage = normalize_workflow_stage(stored_workflow_stage)
    stored_phase = normalize_workflow_phase(
        next_summary.get("workflow_phase"),
        default=_derive_workflow_phase_from_payload(
            payload,
            stored_workflow_stage=stored_stage,
            applied=applied,
        ),
    )
    workflow_stage = normalize_workflow_stage(
        next_summary.get("workflow_stage"),
        default=stored_stage,
    )

    inconsistency_code: str | None = None
    inconsistency_message: str | None = None
    effective_workflow_stage = workflow_stage
    effective_workflow_phase = stored_phase

    if workflow_stage == "completed" and validation_stage != "valid":
        inconsistency_code = "COMPLETED_BUT_NOT_VALID"
        inconsistency_message = (
            "Record was stored as completed even though validation is not valid."
        )
        effective_workflow_stage = "review"
        effective_workflow_phase = "ready_for_apply"
    elif workflow_stage == "completed" and materialization_stage != "applied":
        inconsistency_code = "COMPLETED_WITHOUT_FULL_MATERIALIZATION"
        inconsistency_message = (
            "Record was stored as completed without a fully applied materialization state."
        )
        effective_workflow_stage = "review"
        effective_workflow_phase = "ready_for_apply"
    elif workflow_stage != "completed" and validation_stage == "valid" and materialization_stage == "applied":
        inconsistency_code = "VALID_APPLIED_STAGE_NOT_COMPLETED"
        inconsistency_message = (
            "Record is fully valid and applied, but the stored workflow stage was not completed."
        )
        effective_workflow_stage = "completed"
        effective_workflow_phase = "completed"
    elif workflow_stage == "review" and stored_phase in {"collecting", "planning"}:
        inconsistency_code = "REVIEW_WITH_NON_READY_PHASE"
        inconsistency_message = (
            "Record was stored as review even though the workflow phase was not ready for apply."
        )
        effective_workflow_stage = "in_progress"
        effective_workflow_phase = stored_phase
    elif workflow_stage == "in_progress" and stored_phase == "ready_for_apply":
        inconsistency_code = "READY_PHASE_STAGE_NOT_REVIEW"
        inconsistency_message = (
            "Record reached the ready-for-apply phase, but the stored workflow stage was not review."
        )
        effective_workflow_stage = "review"
        effective_workflow_phase = "ready_for_apply"

    if effective_workflow_stage == "completed":
        effective_workflow_phase = "completed"

    warnings = next_summary.get("validation_warnings")
    if not isinstance(warnings, list):
        warnings = next_summary.get("distribution_validation_warnings")
    normalized_warnings = warnings if isinstance(warnings, list) else []
    next_summary["validation_warnings"] = normalized_warnings
    next_summary["distribution_validation_warnings"] = normalized_warnings

    next_summary["stored_workflow_stage"] = stored_stage
    next_summary["stored_workflow_phase"] = stored_phase
    next_summary["workflow_stage"] = effective_workflow_stage
    next_summary["workflow_phase"] = effective_workflow_phase
    next_summary["validation_stage"] = validation_stage
    next_summary["materialization_stage"] = materialization_stage
    next_summary["state_is_consistent"] = inconsistency_code is None
    next_summary["state_inconsistency_code"] = inconsistency_code
    next_summary["state_inconsistency_message"] = inconsistency_message
    return next_summary


def build_onboarding_materialized_state(
    summary: dict[str, Any] | None,
    *,
    applied: bool = True,
    workflow_stage: str = "completed",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_stage = normalize_workflow_stage(workflow_stage, default="review")
    return {
        "applied": bool(applied),
        "applied_at": datetime.now(timezone.utc).isoformat() if applied else None,
        "summary": build_materialized_summary(
            summary,
            stored_workflow_stage=normalized_stage,
            applied=bool(applied),
            payload=payload,
        ),
    }


def normalize_record_payload_for_response(
    payload: dict[str, Any] | None,
    *,
    stored_workflow_stage: str,
) -> tuple[dict[str, Any], str]:
    next_payload = dict(payload) if isinstance(payload, dict) else {}
    next_payload["answers"] = normalize_onboarding_answers(next_payload.get("answers"))
    next_payload["draft_objects"] = normalize_onboarding_draft_objects(
        next_payload.get("draft_objects"),
        answers=next_payload["answers"],
        stored_workflow_stage=stored_workflow_stage,
    )
    materialized_state = next_payload.get("materialized_state")
    if isinstance(materialized_state, dict):
        applied = bool(materialized_state.get("applied"))
        next_materialized_state = dict(materialized_state)
        next_materialized_state["applied"] = applied
        next_materialized_state["summary"] = build_materialized_summary(
            next_materialized_state.get("summary"),
            stored_workflow_stage=stored_workflow_stage,
            applied=applied,
            payload=next_payload,
        )
        next_payload["materialized_state"] = next_materialized_state
        return next_payload, next_materialized_state["summary"]["workflow_stage"]

    synthesized = build_onboarding_materialized_state(
        {},
        applied=False,
        workflow_stage=stored_workflow_stage,
        payload=next_payload,
    )
    next_payload["materialized_state"] = synthesized
    return next_payload, synthesized["summary"]["workflow_stage"]


def has_valid_applied_materialized_state(payload: dict[str, Any] | None) -> bool:
    normalized_payload, _ = normalize_record_payload_for_response(
        payload,
        stored_workflow_stage="review",
    )
    materialized_state = normalized_payload.get("materialized_state")
    if not isinstance(materialized_state, dict):
        return False
    if not bool(materialized_state.get("applied")):
        return False
    summary = materialized_state.get("summary")
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("validation_stage") == "valid"
        and summary.get("materialization_stage") == "applied"
    )


def coerce_record_stage_for_write(
    stage: Any,
    *,
    payload: dict[str, Any] | None = None,
) -> str:
    normalized = normalize_workflow_stage(stage, default="in_progress")
    if normalized == "completed":
        normalized = "review"
    if isinstance(payload, dict):
        workflow_phase = _derive_workflow_phase_from_payload(
            payload,
            stored_workflow_stage=normalized,
            applied=False,
        )
        if workflow_phase == "ready_for_apply":
            return "review"
        if workflow_phase in {"collecting", "planning"}:
            return "in_progress"
    return normalized
