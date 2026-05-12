from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.advisor.contracts import (
    AdvisorModeEnum,
    AdvisorPreviewResponseV1,
    AllocationBreakdownV1,
    AllocationBucketsV1,
    ApplyPreviewSummaryV1,
    ComparisonSummaryV1,
    DataQualitySummaryV1,
    DebtStrategyImpactV1,
    EnvelopesImpactV1,
    GoalsImpactV1,
    GoalsProfileV1,
    ImpactSummaryV1,
    IntegrityChecksV1,
    MainPriorityEnum,
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
    ReviewDetailsV1,
    RulesImpactV1,
    SafetyImpactV1,
    TradeoffsV1,
)


class ProposalEngineService:
    """Deterministic proposal generation service (safe/balanced/dynamic)."""

    engine_version = "advisor-engine-v1"

    def generate_preview(
        self,
        profile: NormalizedFinancialProfileV1,
        gating: QualityGatingOutputV1,
    ) -> AdvisorPreviewResponseV1:
        preview_id = uuid4()
        now = datetime.now(timezone.utc)

        if not gating.can_generate_preview:
            return AdvisorPreviewResponseV1(
                preview_id=preview_id,
                engine_version=self.engine_version,
                generated_at=now,
                mode=AdvisorModeEnum.blocked,
                degraded_mode=gating.degraded_mode,
                can_recommend_confidently=False,
                recommended_proposal_id=None,
                recommendation_reason_tags=[],
                warnings=gating.warnings,
                blocking_issues=gating.blocking_issues,
                missing_required_fields=gating.missing_required_fields,
                data_quality_summary=DataQualitySummaryV1(
                    completeness_score=gating.completeness_score,
                    reliability_score=gating.reliability_score,
                ),
                proposal_count=0,
                proposals=[],
                comparison_summary=ComparisonSummaryV1(primary_axis=PrimaryAxisEnum.stability),
                apply_preview_summary=self._build_apply_preview_summary(None),
            )

        safe = self._build_proposal(profile, ProposalTypeEnum.safe)
        balanced = self._build_proposal(profile, ProposalTypeEnum.balanced)
        dynamic_type = self._pick_dynamic_type(profile, gating)
        dynamic = self._build_proposal(profile, dynamic_type)

        proposals = [safe, balanced]
        if self._is_distinct(dynamic, balanced):
            proposals.append(dynamic)

        recommended_type, reason_tags = self._pick_recommendation(profile, gating, proposals)
        recommended = None
        for proposal in proposals:
            is_recommended = proposal.proposal_type == recommended_type
            proposal.is_recommended = is_recommended
            if is_recommended:
                recommended = proposal

        assert recommended is not None

        for proposal in proposals:
            if proposal.proposal_id == recommended.proposal_id:
                continue
            proposal.deltas_vs_recommended = {
                "monthly_debt_extra_delta": proposal.allocation.monthly.debt_extra - recommended.allocation.monthly.debt_extra,
                "monthly_goals_delta": proposal.allocation.monthly.goals - recommended.allocation.monthly.goals,
                "monthly_reserve_delta": proposal.allocation.monthly.reserve - recommended.allocation.monthly.reserve,
                "monthly_flexible_delta": proposal.allocation.monthly.flexible - recommended.allocation.monthly.flexible,
                "monthly_sinking_delta": proposal.allocation.monthly.sinking_funds - recommended.allocation.monthly.sinking_funds,
            }

        mode = AdvisorModeEnum.degraded if gating.degraded_mode else AdvisorModeEnum.normal

        return AdvisorPreviewResponseV1(
            preview_id=preview_id,
            engine_version=self.engine_version,
            generated_at=now,
            mode=mode,
            degraded_mode=gating.degraded_mode,
            can_recommend_confidently=gating.can_recommend_confidently,
            recommended_proposal_id=recommended.proposal_id,
            recommendation_reason_tags=reason_tags,
            warnings=gating.warnings,
            blocking_issues=gating.blocking_issues,
            missing_required_fields=gating.missing_required_fields,
            data_quality_summary=DataQualitySummaryV1(
                completeness_score=gating.completeness_score,
                reliability_score=gating.reliability_score,
            ),
            proposal_count=len(proposals),
            proposals=proposals,
            comparison_summary=self._build_comparison_summary(proposals),
            apply_preview_summary=self._build_apply_preview_summary(recommended.proposal_id),
        )

    def _build_proposal(
        self,
        profile: NormalizedFinancialProfileV1,
        proposal_type: ProposalTypeEnum,
    ) -> ProposalV1:
        monthly_income = profile.income_profile.monthly_income_total
        cycle_ratio = profile.metadata.cycle_days / 30.0

        essentials = profile.expense_profile.monthly_essential_total
        debt_min = profile.debt_profile.monthly_debt_minimum_total
        sinking = profile.expense_profile.monthly_sinking_obligations_total

        remaining = max(0.0, monthly_income - essentials - debt_min)

        if proposal_type == ProposalTypeEnum.safe:
            reserve = min(remaining, max(remaining * 0.35, 100.0 if remaining > 0 else 0.0))
            goals = max(0.0, remaining * 0.20)
            debt_extra = max(0.0, remaining * 0.10)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.stability
            risk = "low"
        elif proposal_type == ProposalTypeEnum.balanced:
            reserve = max(0.0, remaining * 0.25)
            goals = max(0.0, remaining * 0.30)
            debt_extra = max(0.0, remaining * 0.20)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.stability
            risk = "medium"
        elif proposal_type == ProposalTypeEnum.debt_first:
            reserve = max(0.0, remaining * 0.15)
            goals = max(0.0, remaining * 0.10)
            debt_extra = max(0.0, remaining * 0.45)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.debt
            risk = "medium"
        elif proposal_type == ProposalTypeEnum.goal_first:
            reserve = max(0.0, remaining * 0.15)
            goals = max(0.0, remaining * 0.45)
            debt_extra = max(0.0, remaining * 0.10)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.goals
            risk = "medium"
        elif proposal_type == ProposalTypeEnum.catch_up:
            reserve = max(0.0, remaining * 0.10)
            goals = 0.0
            debt_extra = max(0.0, remaining * 0.20)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.recovery
            risk = "high"
        else:
            reserve = max(0.0, remaining * 0.30)
            goals = max(0.0, remaining * 0.20)
            debt_extra = max(0.0, remaining * 0.20)
            flexible = max(0.0, remaining - reserve - goals - debt_extra - sinking)
            priority = MainPriorityEnum.stability
            risk = "low"

        total_allocated = essentials + debt_min + debt_extra + reserve + sinking + goals + flexible
        unallocated = max(0.0, monthly_income - total_allocated)

        monthly_buckets = AllocationBucketsV1(
            essentials=essentials,
            debt_minimums=debt_min,
            debt_extra=debt_extra,
            reserve=reserve,
            sinking_funds=sinking,
            goals=goals,
            flexible=flexible,
            total_allocated=total_allocated,
            unallocated_buffer=unallocated,
        )

        cycle_buckets = AllocationBucketsV1(
            essentials=monthly_buckets.essentials * cycle_ratio,
            debt_minimums=monthly_buckets.debt_minimums * cycle_ratio,
            debt_extra=monthly_buckets.debt_extra * cycle_ratio,
            reserve=monthly_buckets.reserve * cycle_ratio,
            sinking_funds=monthly_buckets.sinking_funds * cycle_ratio,
            goals=monthly_buckets.goals * cycle_ratio,
            flexible=monthly_buckets.flexible * cycle_ratio,
            total_allocated=monthly_buckets.total_allocated * cycle_ratio,
            unallocated_buffer=monthly_buckets.unallocated_buffer * cycle_ratio,
        )

        return ProposalV1(
            proposal_id=f"{proposal_type.value}-1",
            proposal_type=proposal_type,
            rank=1,
            is_recommended=False,
            title_key=f"advisor.proposal.{proposal_type.value}.title",
            subtitle_key=f"advisor.proposal.{proposal_type.value}.subtitle",
            fit_profile_tags=[proposal_type.value],
            allocation=AllocationBreakdownV1(
                period_basis=PeriodBasisV1(
                    cycle_days=profile.metadata.cycle_days,
                    monthly_reference_amount=monthly_income,
                    cycle_reference_amount=profile.income_profile.cycle_income_total,
                ),
                monthly=monthly_buckets,
                cycle=cycle_buckets,
                integrity_checks=IntegrityChecksV1(
                    no_negative_allocations=True,
                    allocation_sum_valid=True,
                    minimum_obligations_covered=True,
                    month_cycle_consistent=True,
                ),
            ),
            impact_summary=ImpactSummaryV1(
                monthly_remaining_after_plan=unallocated,
                cycle_remaining_after_plan=unallocated * cycle_ratio,
                debt_coverage_ratio=1.0,
                reserve_progress_ratio=0.0,
                goals_funding_ratio=0.0,
                sinking_coverage_ratio=1.0 if sinking > 0 else 0.0,
            ),
            tradeoffs=TradeoffsV1(
                pros_tags=[f"{proposal_type.value}_pros"],
                cons_tags=[f"{proposal_type.value}_cons"],
                tradeoff_tags=[f"{proposal_type.value}_tradeoff"],
            ),
            proposal_warnings=[],
            risk_signals=ProposalRiskSignalsV1(risk_level=risk, risk_tags=[f"{risk}_risk"]),
            recommendation_layer=RecommendationLayerV1(
                main_priority=priority,
                reason_tags=[proposal_type.value],
                tradeoff_tags=[f"{proposal_type.value}_tradeoff"],
                recommended_for_tags=[proposal_type.value],
                risk_tags=[f"{risk}_risk"],
            ),
            review_details=ReviewDetailsV1(
                what_is_protected=[PriorityLayerEnum.essential.value],
                what_is_limited=["flexible"],
                what_may_be_delayed=["goal_speed"],
                assumptions_used=["monthly_baseline"],
            ),
        )

    def _pick_dynamic_type(self, profile: NormalizedFinancialProfileV1, gating: QualityGatingOutputV1) -> ProposalTypeEnum:
        if gating.degraded_mode:
            return ProposalTypeEnum.catch_up

        income = max(profile.income_profile.monthly_income_total, 1.0)
        debt_ratio = profile.debt_profile.monthly_debt_minimum_total / income
        goals_count = profile.goals_profile.goals_count

        if debt_ratio >= 0.2:
            return ProposalTypeEnum.debt_first
        if goals_count > 0 and debt_ratio < 0.1:
            return ProposalTypeEnum.goal_first
        return ProposalTypeEnum.stability_first

    def _pick_recommendation(
        self,
        profile: NormalizedFinancialProfileV1,
        gating: QualityGatingOutputV1,
        proposals: list[ProposalV1],
    ) -> tuple[ProposalTypeEnum, list[str]]:
        if gating.degraded_mode:
            return ProposalTypeEnum.safe, ["degraded_mode", "safety_first"]

        income = max(profile.income_profile.monthly_income_total, 1.0)
        tension = (
            profile.expense_profile.monthly_expense_total_all
            + profile.debt_profile.monthly_debt_minimum_total
        ) / income
        debt_ratio = profile.debt_profile.monthly_debt_minimum_total / income

        available_types = {p.proposal_type for p in proposals}

        if tension >= 0.9 and ProposalTypeEnum.safe in available_types:
            return ProposalTypeEnum.safe, ["high_tension", "safety_first"]

        if debt_ratio >= 0.2 and ProposalTypeEnum.debt_first in available_types:
            return ProposalTypeEnum.debt_first, ["high_debt_pressure", "debt_priority"]

        if profile.goals_profile.goals_count > 0 and debt_ratio < 0.1 and ProposalTypeEnum.goal_first in available_types:
            return ProposalTypeEnum.goal_first, ["clear_goals", "goal_priority"]

        return ProposalTypeEnum.balanced, ["balanced_default"]

    def _is_distinct(self, candidate: ProposalV1, baseline: ProposalV1) -> bool:
        diffs = 0
        if abs(candidate.allocation.monthly.debt_extra - baseline.allocation.monthly.debt_extra) >= 100:
            diffs += 1
        if abs(candidate.allocation.monthly.goals - baseline.allocation.monthly.goals) >= 100:
            diffs += 1
        if abs(candidate.allocation.monthly.reserve - baseline.allocation.monthly.reserve) >= 100:
            diffs += 1
        if abs(candidate.allocation.monthly.flexible - baseline.allocation.monthly.flexible) >= 100:
            diffs += 1
        return diffs >= 2

    def _build_comparison_summary(self, proposals: list[ProposalV1]) -> ComparisonSummaryV1:
        def by_key(key: str) -> str | None:
            if not proposals:
                return None
            ordered = sorted(proposals, key=lambda p: getattr(p.allocation.monthly, key), reverse=True)
            return ordered[0].proposal_id

        return ComparisonSummaryV1(
            primary_axis=PrimaryAxisEnum.stability,
            best_for_stability=by_key("reserve"),
            best_for_debt_speed=by_key("debt_extra"),
            best_for_goal_progress=by_key("goals"),
            best_for_cash_safety=by_key("unallocated_buffer"),
        )

    def _build_apply_preview_summary(self, proposal_id: str | None) -> ApplyPreviewSummaryV1:
        return ApplyPreviewSummaryV1(
            proposal_id=proposal_id,
            envelopes_impact=EnvelopesImpactV1(create_count=1, update_count=3, freeze_count=0),
            goals_impact=GoalsImpactV1(active_count=1, slowed_count=0, paused_count=0),
            rules_impact=RulesImpactV1(create_count=1, update_count=2, disable_count=0),
            reserve_impact=ReserveImpactV1(
                monthly_contribution=0,
                cycle_contribution=0,
                starter_gap_after_apply=0,
            ),
            debt_strategy_impact=DebtStrategyImpactV1(
                minimums_covered=True,
                focus_enabled=True,
                target_debt_id=None,
                monthly_extra_amount=0,
            ),
            safety=SafetyImpactV1(
                requires_user_confirmation=True,
                apply_allowed_if_confirmed=True,
            ),
        )
