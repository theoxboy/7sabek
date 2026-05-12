from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.schemas.advisor.contracts import (
    AdvisorModeEnum,
    AdvisorPreviewResponseV1,
    AllocationBreakdownV1,
    AllocationBucketsV1,
    ApplyPreviewSummaryV1,
    ComparisonSummaryV1,
    CurrentCashSnapshotV1,
    DataQualitySummaryV1,
    DebtItemV1,
    DebtProfileV1,
    DebtStrategyImpactV1,
    EnvelopesImpactV1,
    ExpenseProfileV1,
    FreshnessSnapshotV1,
    GatingSnapshotV1,
    GoalsImpactV1,
    GoalItemV1,
    GoalsProfileV1,
    ImpactSummaryV1,
    IncomeProfileV1,
    IntegrityChecksV1,
    MainPriorityEnum,
    MetadataV1,
    NormalizedFinancialProfileV1,
    PeriodBasisV1,
    PrimaryAxisEnum,
    PriorityLayerEnum,
    ProposalRiskSignalsV1,
    ProposalTypeEnum,
    ProposalV1,
    QualityGatingOutputV1,
    RecommendationLayerV1,
    ReserveImpactV1,
    ReserveProfileV1,
    ReviewDetailsV1,
    RulesImpactV1,
    SafetyImpactV1,
    SourceContextEnum,
    TradeoffsV1,
)

FIXED_TS = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
FIXED_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
FIXED_PROFILE_ID = UUID("22222222-2222-2222-2222-222222222222")
FIXED_PREVIEW_ID = UUID("33333333-3333-3333-3333-333333333333")


def normal_profile_input() -> NormalizedFinancialProfileV1:
    return NormalizedFinancialProfileV1(
        metadata=MetadataV1(
            profile_id=FIXED_PROFILE_ID,
            user_id=FIXED_USER_ID,
            generated_at=FIXED_TS,
            source_context=SourceContextEnum.onboarding_v2,
            currency="MAD",
            cycle_days=30,
        ),
        income_profile=IncomeProfileV1(monthly_income_total=12000, cycle_income_total=12000),
        expense_profile=ExpenseProfileV1(
            monthly_essential_total=5000,
            monthly_expense_total_all=7000,
            monthly_sinking_obligations_total=700,
        ),
        debt_profile=DebtProfileV1(
            has_debt=True,
            monthly_debt_minimum_total=1200,
            debts=[
                DebtItemV1(
                    debt_id="debt-1",
                    remaining_amount=20000,
                    minimum_payment_native=1200,
                    payment_frequency="monthly",
                    monthly_minimum_equivalent=1200,
                    cycle_minimum_equivalent=1200,
                )
            ],
        ),
        goals_profile=GoalsProfileV1(
            goals_count=1,
            goals_target_total=50000,
            goals=[
                GoalItemV1(
                    goal_id="goal-1",
                    target_amount=50000,
                    current_amount=8000,
                )
            ],
        ),
        reserve_profile=ReserveProfileV1(
            reserve_current_amount=9000,
            reserve_target_starter=12000,
            reserve_gap_to_starter=3000,
        ),
        current_cash_snapshot=CurrentCashSnapshotV1(available_now_amount=2500),
    )


def blocked_profile_input() -> NormalizedFinancialProfileV1:
    profile = normal_profile_input()
    profile.income_profile.monthly_income_total = 0
    profile.income_profile.cycle_income_total = 0
    return profile


def degraded_profile_input() -> NormalizedFinancialProfileV1:
    profile = normal_profile_input()
    profile.reserve_profile.reserve_current_amount = 0
    profile.goals_profile.goals = []
    return profile


def normal_gating_output() -> QualityGatingOutputV1:
    return QualityGatingOutputV1(
        missing_required_fields=[],
        warnings=[],
        blocking_issues=[],
        degraded_mode=False,
        completeness_score=96,
        reliability_score=90,
        can_generate_preview=True,
        can_recommend_confidently=True,
        can_apply=True,
    )


def blocked_gating_output() -> QualityGatingOutputV1:
    return QualityGatingOutputV1(
        missing_required_fields=["income_profile.monthly_income_total", "income_profile.cycle_income_total"],
        warnings=[],
        blocking_issues=["MONTHLY_INCOME_MISSING_OR_INVALID", "CYCLE_INCOME_MISSING_OR_INVALID"],
        degraded_mode=False,
        completeness_score=20,
        reliability_score=20,
        can_generate_preview=False,
        can_recommend_confidently=False,
        can_apply=False,
    )


def degraded_gating_output() -> QualityGatingOutputV1:
    return QualityGatingOutputV1(
        missing_required_fields=[],
        warnings=["reserve weak", "goals partial"],
        blocking_issues=[],
        degraded_mode=True,
        completeness_score=82,
        reliability_score=63,
        can_generate_preview=True,
        can_recommend_confidently=False,
        can_apply=False,
    )


def _proposal(proposal_id: str, proposal_type: ProposalTypeEnum, recommended: bool) -> ProposalV1:
    buckets = AllocationBucketsV1(
        essentials=5000,
        debt_minimums=1200,
        debt_extra=500 if proposal_type == ProposalTypeEnum.debt_first else 250,
        reserve=700 if proposal_type == ProposalTypeEnum.safe else 400,
        sinking_funds=700,
        goals=600 if proposal_type == ProposalTypeEnum.goal_first else 300,
        flexible=2000,
        total_allocated=10300,
        unallocated_buffer=1700,
    )
    return ProposalV1(
        proposal_id=proposal_id,
        proposal_type=proposal_type,
        rank=1,
        is_recommended=recommended,
        title_key=f"advisor.proposal.{proposal_type.value}.title",
        subtitle_key="Priorité stabilité",
        fit_profile_tags=[proposal_type.value],
        allocation=AllocationBreakdownV1(
            period_basis=PeriodBasisV1(cycle_days=30, monthly_reference_amount=12000, cycle_reference_amount=12000),
            monthly=buckets,
            cycle=buckets,
            integrity_checks=IntegrityChecksV1(
                no_negative_allocations=True,
                allocation_sum_valid=True,
                minimum_obligations_covered=True,
                month_cycle_consistent=True,
            ),
        ),
        impact_summary=ImpactSummaryV1(
            monthly_remaining_after_plan=1700,
            cycle_remaining_after_plan=1700,
            debt_coverage_ratio=1,
            reserve_progress_ratio=0.3,
            goals_funding_ratio=0.2,
            sinking_coverage_ratio=1,
        ),
        tradeoffs=TradeoffsV1(pros_tags=["stable"], cons_tags=["slower_goals"], tradeoff_tags=["moderate_tradeoff"]),
        proposal_warnings=[],
        risk_signals=ProposalRiskSignalsV1(risk_level="medium", risk_tags=["moderate_risk"]),
        recommendation_layer=RecommendationLayerV1(
            main_priority=MainPriorityEnum.stability,
            reason_tags=["stable_income"],
            tradeoff_tags=["moderate_goal_speed"],
            recommended_for_tags=["profil stable"],
            risk_tags=["moderate_risk"],
        ),
        review_details=ReviewDetailsV1(
            what_is_protected=[PriorityLayerEnum.essential.value],
            what_is_limited=["debt_extra"],
            what_may_be_delayed=["goal_speed"],
            assumptions_used=["monthly_baseline"],
        ),
    )


def normal_preview_output() -> AdvisorPreviewResponseV1:
    proposals = [
        _proposal("safe-1", ProposalTypeEnum.safe, False),
        _proposal("balanced-1", ProposalTypeEnum.balanced, True),
        _proposal("debt-first-1", ProposalTypeEnum.debt_first, False),
    ]
    return AdvisorPreviewResponseV1(
        preview_id=FIXED_PREVIEW_ID,
        engine_version="advisor-engine-v1",
        generated_at=FIXED_TS,
        mode=AdvisorModeEnum.normal,
        degraded_mode=False,
        can_recommend_confidently=True,
        recommended_proposal_id="balanced-1",
        recommendation_reason_tags=["balanced_default"],
        warnings=[],
        blocking_issues=[],
        missing_required_fields=[],
        data_quality_summary=DataQualitySummaryV1(completeness_score=96, reliability_score=90),
        proposal_count=3,
        proposals=proposals,
        comparison_summary=ComparisonSummaryV1(
            primary_axis=PrimaryAxisEnum.stability,
            best_for_stability="safe-1",
            best_for_debt_speed="debt-first-1",
            best_for_goal_progress="balanced-1",
            best_for_cash_safety="safe-1",
        ),
        apply_preview_summary=ApplyPreviewSummaryV1(
            proposal_id="balanced-1",
            envelopes_impact=EnvelopesImpactV1(create_count=1, update_count=2, freeze_count=0),
            goals_impact=GoalsImpactV1(active_count=1, slowed_count=0, paused_count=0),
            rules_impact=RulesImpactV1(create_count=1, update_count=1, disable_count=0),
            reserve_impact=ReserveImpactV1(monthly_contribution=400, cycle_contribution=400, starter_gap_after_apply=2600),
            debt_strategy_impact=DebtStrategyImpactV1(
                minimums_covered=True,
                focus_enabled=True,
                target_debt_id=None,
                monthly_extra_amount=250,
            ),
            safety=SafetyImpactV1(requires_user_confirmation=True, apply_allowed_if_confirmed=True),
        ),
    )


def blocked_preview_output() -> AdvisorPreviewResponseV1:
    return AdvisorPreviewResponseV1(
        preview_id=FIXED_PREVIEW_ID,
        engine_version="advisor-engine-v1",
        generated_at=FIXED_TS,
        mode=AdvisorModeEnum.blocked,
        degraded_mode=False,
        can_recommend_confidently=False,
        recommended_proposal_id=None,
        recommendation_reason_tags=[],
        warnings=[],
        blocking_issues=["MONTHLY_INCOME_MISSING_OR_INVALID"],
        missing_required_fields=["income_profile.monthly_income_total"],
        data_quality_summary=DataQualitySummaryV1(completeness_score=20, reliability_score=20),
        proposal_count=0,
        proposals=[],
        comparison_summary=ComparisonSummaryV1(primary_axis=PrimaryAxisEnum.stability),
        apply_preview_summary=ApplyPreviewSummaryV1(
            proposal_id=None,
            envelopes_impact=EnvelopesImpactV1(create_count=0, update_count=0, freeze_count=0),
            goals_impact=GoalsImpactV1(active_count=0, slowed_count=0, paused_count=0),
            rules_impact=RulesImpactV1(create_count=0, update_count=0, disable_count=0),
            reserve_impact=ReserveImpactV1(monthly_contribution=0, cycle_contribution=0, starter_gap_after_apply=0),
            debt_strategy_impact=DebtStrategyImpactV1(
                minimums_covered=False,
                focus_enabled=False,
                target_debt_id=None,
                monthly_extra_amount=0,
            ),
            safety=SafetyImpactV1(requires_user_confirmation=True, apply_allowed_if_confirmed=False),
        ),
    )


def degraded_preview_output() -> AdvisorPreviewResponseV1:
    preview = normal_preview_output()
    preview.mode = AdvisorModeEnum.degraded
    preview.degraded_mode = True
    preview.can_recommend_confidently = False
    preview.warnings = ["reserve weak", "goals partial"]
    preview.data_quality_summary = DataQualitySummaryV1(completeness_score=82, reliability_score=63)
    return preview


def sample_validation_freshness() -> FreshnessSnapshotV1:
    return FreshnessSnapshotV1(
        is_stale=False,
        current_profile_hash="hash-current",
        preview_profile_hash="hash-current",
        current_engine_version="advisor-engine-v1",
        preview_engine_version="advisor-engine-v1",
    )


def sample_validation_gating() -> GatingSnapshotV1:
    return GatingSnapshotV1(degraded_mode=False, can_recommend_confidently=True)
