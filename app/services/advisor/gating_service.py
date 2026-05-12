from __future__ import annotations

from app.schemas.advisor.contracts import (
    NormalizedFinancialProfileV1,
    QualityGatingOutputV1,
)


class GatingService:
    """Computes quality/gating output from a normalized profile."""

    def evaluate(self, profile: NormalizedFinancialProfileV1) -> QualityGatingOutputV1:
        missing_required_fields: list[str] = []
        warnings: list[str] = []
        blocking_issues: list[str] = []

        if profile.metadata.cycle_days <= 0:
            missing_required_fields.append("metadata.cycle_days")
            blocking_issues.append("CYCLE_DAYS_INVALID")

        if profile.income_profile.monthly_income_total <= 0:
            missing_required_fields.append("income_profile.monthly_income_total")
            blocking_issues.append("MONTHLY_INCOME_MISSING_OR_INVALID")

        if profile.income_profile.cycle_income_total <= 0:
            missing_required_fields.append("income_profile.cycle_income_total")
            blocking_issues.append("CYCLE_INCOME_MISSING_OR_INVALID")

        if profile.expense_profile.monthly_essential_total < 0:
            missing_required_fields.append("expense_profile.monthly_essential_total")
            blocking_issues.append("ESSENTIALS_INVALID")

        if profile.expense_profile.monthly_expense_total_all < 0:
            missing_required_fields.append("expense_profile.monthly_expense_total_all")
            blocking_issues.append("EXPENSE_TOTAL_INVALID")

        if profile.current_cash_snapshot.available_now_amount < 0:
            missing_required_fields.append("current_cash_snapshot.available_now_amount")
            blocking_issues.append("CURRENT_CASH_INVALID")

        if profile.debt_profile.has_debt and profile.debt_profile.monthly_debt_minimum_total <= 0:
            missing_required_fields.append("debt_profile.monthly_debt_minimum_total")
            blocking_issues.append("DEBT_MINIMUM_INVALID")

        # Degraded signals.
        if profile.reserve_profile.reserve_current_amount <= 0:
            warnings.append("reserve weak")

        if profile.expense_profile.monthly_sinking_obligations_total <= 0:
            warnings.append("sinking obligations unknown")

        if profile.debt_profile.has_debt and len(profile.debt_profile.debts) == 0:
            warnings.append("debt details partial")

        if profile.goals_profile.goals_count > 0 and len(profile.goals_profile.goals) == 0:
            warnings.append("goals partial")

        income = max(profile.income_profile.monthly_income_total, 1.0)
        tension_ratio = (
            profile.expense_profile.monthly_expense_total_all
            + profile.debt_profile.monthly_debt_minimum_total
        ) / income
        if tension_ratio >= 0.9:
            warnings.append("high tension")

        degraded_mode = bool(warnings)
        can_generate_preview = len(blocking_issues) == 0

        completeness_score = 100.0
        completeness_score -= 25.0 * len(set(missing_required_fields))
        completeness_score -= 8.0 * len(set(warnings))
        completeness_score = max(0.0, min(100.0, completeness_score))

        reliability_score = 100.0
        if tension_ratio >= 0.9:
            reliability_score -= 20.0
        if profile.reserve_profile.reserve_current_amount <= 0:
            reliability_score -= 15.0
        if profile.debt_profile.has_debt and len(profile.debt_profile.debts) == 0:
            reliability_score -= 15.0
        reliability_score -= 10.0 if profile.goals_profile.goals_count > 0 and len(profile.goals_profile.goals) == 0 else 0.0
        reliability_score = max(0.0, min(100.0, reliability_score))

        can_recommend_confidently = can_generate_preview and (not degraded_mode) and reliability_score >= 70.0

        return QualityGatingOutputV1(
            missing_required_fields=sorted(set(missing_required_fields)),
            warnings=sorted(set(warnings)),
            blocking_issues=sorted(set(blocking_issues)),
            degraded_mode=degraded_mode,
            completeness_score=completeness_score,
            reliability_score=reliability_score,
            can_generate_preview=can_generate_preview,
            can_recommend_confidently=can_recommend_confidently,
            can_apply=can_recommend_confidently,
        )
