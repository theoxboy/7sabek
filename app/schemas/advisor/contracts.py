from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AdvisorBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrequencyEnum(str, Enum):
    weekly = "weekly"
    biweekly = "biweekly"
    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"
    custom = "custom"


class DebtPaymentStyleEnum(str, Enum):
    minimum = "minimum"
    fixed = "fixed"
    variable = "variable"


class DebtStatusHealthEnum(str, Enum):
    current = "current"
    at_risk = "at_risk"
    late = "late"
    unknown = "unknown"


class GoalStatusEnum(str, Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    paused = "paused"


class GoalPriorityHintEnum(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PriorityLayerEnum(str, Enum):
    essential = "essential"
    important = "important"
    discretionary = "discretionary"


class SourceContextEnum(str, Enum):
    onboarding_v2 = "onboarding_v2"
    advisor_refresh = "advisor_refresh"
    system_rebuild = "system_rebuild"


class RiskLevelEnum(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ProposalTypeEnum(str, Enum):
    safe = "safe"
    balanced = "balanced"
    debt_first = "debt_first"
    goal_first = "goal_first"
    stability_first = "stability_first"
    catch_up = "catch_up"


class MainPriorityEnum(str, Enum):
    stability = "stability"
    debt = "debt"
    goals = "goals"
    recovery = "recovery"


class AdvisorModeEnum(str, Enum):
    normal = "normal"
    degraded = "degraded"
    blocked = "blocked"


class PrimaryAxisEnum(str, Enum):
    stability = "stability"
    debt_speed = "debt_speed"
    goal_progress = "goal_progress"
    cash_safety = "cash_safety"


class AdvisorDecisionStatusEnum(str, Enum):
    accepted = "accepted"
    rejected = "rejected"
    invalidated = "invalidated"
    expired = "expired"
    consumed = "consumed"


class IncomeStreamV1(AdvisorBaseModel):
    stream_id: str
    label: Optional[str] = None
    amount_native: float = Field(ge=0)
    frequency: FrequencyEnum
    monthly_equivalent: float = Field(ge=0)
    cycle_equivalent: float = Field(ge=0)
    is_variable: Optional[bool] = None


class ExpenseItemV1(AdvisorBaseModel):
    expense_id: str
    label: Optional[str] = None
    category: Optional[str] = None
    priority_layer: Optional[PriorityLayerEnum] = None
    amount_native: float = Field(ge=0)
    frequency: FrequencyEnum
    monthly_equivalent: float = Field(ge=0)
    cycle_equivalent: float = Field(ge=0)
    is_sinking_obligation: Optional[bool] = None


class DebtItemV1(AdvisorBaseModel):
    debt_id: str
    label: Optional[str] = None
    debt_type: Optional[str] = None
    remaining_amount: float = Field(ge=0)
    minimum_payment_native: float = Field(ge=0)
    payment_frequency: FrequencyEnum
    monthly_minimum_equivalent: float = Field(ge=0)
    cycle_minimum_equivalent: float = Field(ge=0)
    payment_style: Optional[DebtPaymentStyleEnum] = None
    status_health: Optional[DebtStatusHealthEnum] = None
    target_date: Optional[date] = None
    interest_rate_apr: Optional[float] = Field(default=None, ge=0)


class GoalItemV1(AdvisorBaseModel):
    goal_id: str
    label: Optional[str] = None
    goal_type: Optional[str] = None
    target_amount: float = Field(ge=0)
    current_amount: Optional[float] = Field(default=None, ge=0)
    target_date: Optional[date] = None
    status: Optional[GoalStatusEnum] = None
    priority_hint: Optional[GoalPriorityHintEnum] = None


class MetadataV1(AdvisorBaseModel):
    schema_version: Literal["NormalizedFinancialProfileV1"] = "NormalizedFinancialProfileV1"
    profile_id: UUID
    user_id: UUID
    generated_at: datetime
    source_context: SourceContextEnum
    currency: str
    cycle_days: float = Field(gt=0)
    source_snapshot_at: Optional[datetime] = None


class IncomeProfileV1(AdvisorBaseModel):
    monthly_income_total: float = Field(ge=0)
    cycle_income_total: float = Field(ge=0)
    income_streams: list[IncomeStreamV1] = Field(default_factory=list)


class ExpenseProfileV1(AdvisorBaseModel):
    monthly_essential_total: float = Field(ge=0)
    monthly_expense_total_all: float = Field(ge=0)
    monthly_sinking_obligations_total: float = Field(ge=0)
    expenses: list[ExpenseItemV1] = Field(default_factory=list)


class DebtProfileV1(AdvisorBaseModel):
    has_debt: bool
    monthly_debt_minimum_total: float = Field(ge=0)
    debts: list[DebtItemV1] = Field(default_factory=list)


class GoalsProfileV1(AdvisorBaseModel):
    goals_count: int = Field(ge=0)
    goals_target_total: float = Field(ge=0)
    goals_started_count: Optional[int] = Field(default=None, ge=0)
    goals_with_target_date_count: Optional[int] = Field(default=None, ge=0)
    goals: list[GoalItemV1] = Field(default_factory=list)


class ReserveProfileV1(AdvisorBaseModel):
    reserve_current_amount: float = Field(ge=0)
    reserve_target_starter: float = Field(ge=0)
    reserve_gap_to_starter: float = Field(ge=0)


class CurrentCashSnapshotV1(AdvisorBaseModel):
    available_now_amount: float = Field(ge=0)
    captured_at: Optional[datetime] = None


class RiskIndicatorsV1(AdvisorBaseModel):
    budget_tension_ratio: Optional[float] = None
    debt_pressure_ratio: Optional[float] = None
    reserve_coverage_ratio: Optional[float] = None
    overall_risk_level: Optional[RiskLevelEnum] = None
    tags: list[str] = Field(default_factory=list)


class DataQualityV1(AdvisorBaseModel):
    completeness_inputs_count: Optional[int] = Field(default=None, ge=0)
    reliability_flags_count: Optional[int] = Field(default=None, ge=0)
    notes: list[str] = Field(default_factory=list)


class DerivedTotalsV1(AdvisorBaseModel):
    monthly_remaining_before_plan: float
    cycle_remaining_before_plan: float


class NormalizedFinancialProfileV1(AdvisorBaseModel):
    metadata: MetadataV1
    income_profile: IncomeProfileV1
    expense_profile: ExpenseProfileV1
    debt_profile: DebtProfileV1
    goals_profile: GoalsProfileV1
    reserve_profile: ReserveProfileV1
    current_cash_snapshot: CurrentCashSnapshotV1
    risk_indicators: Optional[RiskIndicatorsV1] = None
    data_quality: Optional[DataQualityV1] = None
    derived_totals: Optional[DerivedTotalsV1] = None


class QualityGatingOutputV1(AdvisorBaseModel):
    missing_required_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    degraded_mode: bool
    completeness_score: float = Field(ge=0, le=100)
    reliability_score: float = Field(ge=0, le=100)
    can_generate_preview: bool
    can_recommend_confidently: bool
    can_apply: bool


class AllocationBucketsV1(AdvisorBaseModel):
    essentials: float = Field(ge=0)
    debt_minimums: float = Field(ge=0)
    debt_extra: float = Field(ge=0)
    reserve: float = Field(ge=0)
    sinking_funds: float = Field(ge=0)
    goals: float = Field(ge=0)
    flexible: float = Field(ge=0)
    total_allocated: float = Field(ge=0)
    unallocated_buffer: float = Field(ge=0)


class PeriodBasisV1(AdvisorBaseModel):
    cycle_days: float = Field(gt=0)
    monthly_reference_amount: float = Field(ge=0)
    cycle_reference_amount: float = Field(ge=0)


class IntegrityChecksV1(AdvisorBaseModel):
    no_negative_allocations: bool
    allocation_sum_valid: bool
    minimum_obligations_covered: bool
    month_cycle_consistent: bool


class AllocationBreakdownV1(AdvisorBaseModel):
    period_basis: PeriodBasisV1
    monthly: AllocationBucketsV1
    cycle: AllocationBucketsV1
    integrity_checks: IntegrityChecksV1


class ImpactSummaryV1(AdvisorBaseModel):
    monthly_remaining_after_plan: float
    cycle_remaining_after_plan: float
    debt_coverage_ratio: float
    reserve_progress_ratio: float
    goals_funding_ratio: float
    sinking_coverage_ratio: float


class TradeoffsV1(AdvisorBaseModel):
    pros_tags: list[str] = Field(default_factory=list)
    cons_tags: list[str] = Field(default_factory=list)
    tradeoff_tags: list[str] = Field(default_factory=list)


class ProposalRiskSignalsV1(AdvisorBaseModel):
    risk_level: RiskLevelEnum
    risk_tags: list[str] = Field(default_factory=list)


class ProposalDeltasVsRecommendedV1(AdvisorBaseModel):
    monthly_debt_extra_delta: float
    monthly_goals_delta: float
    monthly_reserve_delta: float
    monthly_flexible_delta: float
    monthly_sinking_delta: float


class RecommendationLayerV1(AdvisorBaseModel):
    main_priority: MainPriorityEnum
    reason_tags: list[str] = Field(default_factory=list)
    tradeoff_tags: list[str] = Field(default_factory=list)
    recommended_for_tags: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)


class ReviewDetailsV1(AdvisorBaseModel):
    what_is_protected: list[str] = Field(default_factory=list)
    what_is_limited: list[str] = Field(default_factory=list)
    what_may_be_delayed: list[str] = Field(default_factory=list)
    assumptions_used: list[str] = Field(default_factory=list)


class ProposalV1(AdvisorBaseModel):
    proposal_id: str
    proposal_type: ProposalTypeEnum
    rank: int = Field(ge=1)
    is_recommended: bool
    title_key: Optional[str] = None
    subtitle_key: Optional[str] = None
    fit_profile_tags: list[str] = Field(default_factory=list)
    allocation: AllocationBreakdownV1
    impact_summary: ImpactSummaryV1
    tradeoffs: TradeoffsV1
    proposal_warnings: list[str] = Field(default_factory=list)
    risk_signals: ProposalRiskSignalsV1
    deltas_vs_recommended: Optional[ProposalDeltasVsRecommendedV1] = None
    recommendation_layer: RecommendationLayerV1
    review_details: ReviewDetailsV1


class EnvelopesImpactV1(AdvisorBaseModel):
    create_count: int = Field(ge=0)
    update_count: int = Field(ge=0)
    freeze_count: int = Field(ge=0)


class GoalsImpactV1(AdvisorBaseModel):
    active_count: int = Field(ge=0)
    slowed_count: int = Field(ge=0)
    paused_count: int = Field(ge=0)


class RulesImpactV1(AdvisorBaseModel):
    create_count: int = Field(ge=0)
    update_count: int = Field(ge=0)
    disable_count: int = Field(ge=0)


class ReserveImpactV1(AdvisorBaseModel):
    monthly_contribution: float = Field(ge=0)
    cycle_contribution: float = Field(ge=0)
    starter_gap_after_apply: float = Field(ge=0)


class DebtStrategyImpactV1(AdvisorBaseModel):
    minimums_covered: bool
    focus_enabled: bool
    target_debt_id: Optional[str] = None
    monthly_extra_amount: float = Field(ge=0)


class SafetyImpactV1(AdvisorBaseModel):
    requires_user_confirmation: Literal[True] = True
    apply_allowed_if_confirmed: bool


class ApplyPreviewSummaryV1(AdvisorBaseModel):
    proposal_id: Optional[str] = None
    envelopes_impact: EnvelopesImpactV1
    goals_impact: GoalsImpactV1
    rules_impact: RulesImpactV1
    reserve_impact: ReserveImpactV1
    debt_strategy_impact: DebtStrategyImpactV1
    safety: SafetyImpactV1


class DataQualitySummaryV1(AdvisorBaseModel):
    completeness_score: float = Field(ge=0, le=100)
    reliability_score: float = Field(ge=0, le=100)


class ComparisonSummaryV1(AdvisorBaseModel):
    primary_axis: PrimaryAxisEnum
    best_for_stability: Optional[str] = None
    best_for_debt_speed: Optional[str] = None
    best_for_goal_progress: Optional[str] = None
    best_for_cash_safety: Optional[str] = None


class AdvisorPreviewResponseV1(AdvisorBaseModel):
    preview_id: UUID
    engine_version: str
    generated_at: datetime
    mode: AdvisorModeEnum
    degraded_mode: bool
    can_recommend_confidently: bool
    recommended_proposal_id: Optional[str] = None
    recommendation_reason_tags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    data_quality_summary: DataQualitySummaryV1
    proposal_count: int = Field(ge=0)
    proposals: list[ProposalV1] = Field(default_factory=list)
    comparison_summary: ComparisonSummaryV1
    apply_preview_summary: ApplyPreviewSummaryV1


class FreshnessSnapshotV1(AdvisorBaseModel):
    is_stale: bool
    current_profile_hash: str
    preview_profile_hash: str
    current_engine_version: str
    preview_engine_version: str


class GatingSnapshotV1(AdvisorBaseModel):
    degraded_mode: bool
    can_recommend_confidently: bool


class AdvisorPreApplyValidationResultV1(AdvisorBaseModel):
    ok: bool
    can_apply: bool
    validation_id: Optional[UUID] = None
    reasons: list[str] = Field(default_factory=list)
    freshness: FreshnessSnapshotV1
    gating_snapshot: GatingSnapshotV1


class AdvisorDecisionV1(AdvisorBaseModel):
    decision_id: UUID
    user_id: UUID
    preview_id: UUID
    proposal_id: str
    validation_id: UUID
    status: AdvisorDecisionStatusEnum
    accepted_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
    profile_hash_at_accept: str
    engine_version_at_accept: str
    apply_ready: bool
    consumed_by_apply_id: Optional[UUID] = None
