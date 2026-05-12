from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OnboardingV2RecordCreateIn(BaseModel):
    flow_version: str = Field(default="v2", max_length=32)
    stage: str = Field(default="in_progress", max_length=20)
    answers: dict[str, Any] = Field(default_factory=dict)
    draft_objects: dict[str, Any] = Field(default_factory=dict)


class OnboardingV2RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    flow_version: str
    stage: str
    income_type: Optional[str] = None
    primary_objective: Optional[str] = None
    household_type: Optional[str] = None
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class OnboardingV2AdminRecordOut(OnboardingV2RecordOut):
    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None


class OnboardingV2AdminRecordListOut(BaseModel):
    items: list[OnboardingV2AdminRecordOut]


class OnboardingV2ApplyOut(BaseModel):
    record_id: UUID
    applied: bool = True
    workflow_stage: Optional[str] = None
    workflow_phase: Optional[str] = None
    validation_stage: Optional[str] = None
    materialization_stage: Optional[str] = None
    state_is_consistent: bool = True
    state_inconsistency_code: Optional[str] = None
    state_inconsistency_message: Optional[str] = None
    selected_envelopes_count: int = 0
    selected_rollover_on_count: int = 0
    envelopes_created: int = 0
    envelopes_updated: int = 0
    categories_created: int = 0
    mappings_upserted: int = 0
    goals_created: int = 0
    goals_updated: int = 0
    sinking_funds_created: int = 0
    sinking_funds_updated: int = 0
    distribution_rules_created: int = 0
    goal_distribution_rules_created: int = 0
    distribution_auto_enabled: bool = False
    distribution_posture_v1: dict[str, Any] = Field(default_factory=dict)
    financial_priority_profile: dict[str, Any] = Field(default_factory=dict)
    debt_posture: Optional[str] = None
    goal_posture: Optional[str] = None
    living_margin_level: Optional[str] = None
    reserve_policy: Optional[str] = None
    reserve_level: Optional[str] = None
    confidence_label: Optional[str] = None
    sinking_fund_policy: dict[str, Any] = Field(default_factory=dict)
    cash_flow_timing_v1: dict[str, Any] = Field(default_factory=dict)
    reserve_plan_v1: dict[str, Any] = Field(default_factory=dict)
    priority_explanation_lines: list[str] = Field(default_factory=list)
    distribution_setup_valid: bool = False
    distribution_setup_source: Optional[str] = None
    distribution_status: Optional[str] = None
    distribution_source: Optional[str] = None
    validation_warnings: list[str] = Field(default_factory=list)
    distribution_validation_warnings: list[str] = Field(default_factory=list)
    distribution_eligible_total: int = 0
    distribution_covered_total: int = 0
    distribution_unresolved_total: int = 0
    distribution_unresolved_envelope_names: list[str] = Field(default_factory=list)
    distribution_missing_envelope_names: list[str] = Field(default_factory=list)
    distribution_active_config_id: Optional[str] = None
