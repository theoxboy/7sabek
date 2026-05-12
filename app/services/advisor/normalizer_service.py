from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal
from app.models.onboarding_v2_record import OnboardingV2Record
from app.models.user import User
from app.schemas.advisor.contracts import (
    CurrentCashSnapshotV1,
    DataQualityV1,
    DebtProfileV1,
    DerivedTotalsV1,
    ExpenseProfileV1,
    GoalsProfileV1,
    IncomeProfileV1,
    MetadataV1,
    NormalizedFinancialProfileV1,
    ReserveProfileV1,
    SourceContextEnum,
)


class NormalizerService:
    """Builds NormalizedFinancialProfileV1 from persisted user data."""

    async def build_profile(self, db: AsyncSession, user: User) -> NormalizedFinancialProfileV1:
        onboarding = await self._load_latest_onboarding(db, user.id)
        goals = await self._load_goals(db, user.id)
        payload = onboarding.payload if onboarding else {}

        cycle_days = self._resolve_cycle_days(user, payload)
        monthly_income_total, income_debug = self._resolve_monthly_income(payload)
        cycle_income_total = self._monthly_to_cycle(monthly_income_total, cycle_days)

        monthly_essential_total = self._resolve_monthly_essentials(payload)
        monthly_expense_total_all = self._resolve_monthly_total_expenses(payload, monthly_essential_total)
        monthly_sinking_total = self._resolve_monthly_sinking(payload)
        monthly_debt_minimum_total = self._resolve_monthly_debt_minimum(payload)

        reserve_current = self._resolve_reserve_current(payload)
        reserve_starter = self._resolve_reserve_starter_target(payload, monthly_essential_total)
        reserve_gap = max(0.0, reserve_starter - reserve_current)

        available_now = self._resolve_available_now(payload)

        goals_target_total = float(
            sum(Decimal(str(goal.target_amount)) for goal in goals)
        ) if goals else self._resolve_goals_target_total(payload)

        goals_started_count = sum(1 for goal in goals if Decimal(str(goal.contribution_amount)) > 0)
        goals_with_date = sum(1 for goal in goals if goal.target_date is not None)

        source_context: SourceContextEnum = (
            SourceContextEnum.onboarding_v2 if onboarding is not None else SourceContextEnum.advisor_refresh
        )

        monthly_remaining = monthly_income_total - monthly_expense_total_all - monthly_debt_minimum_total
        cycle_remaining = self._monthly_to_cycle(monthly_remaining, cycle_days)

        return NormalizedFinancialProfileV1(
            metadata=MetadataV1(
                profile_id=uuid4(),
                user_id=user.id,
                generated_at=datetime.now(timezone.utc),
                source_context=source_context,
                currency=user.currency or "MAD",
                cycle_days=cycle_days,
                source_snapshot_at=onboarding.updated_at if onboarding else None,
            ),
            income_profile=IncomeProfileV1(
                monthly_income_total=monthly_income_total,
                cycle_income_total=cycle_income_total,
                income_streams=[],
            ),
            expense_profile=ExpenseProfileV1(
                monthly_essential_total=monthly_essential_total,
                monthly_expense_total_all=monthly_expense_total_all,
                monthly_sinking_obligations_total=monthly_sinking_total,
                expenses=[],
            ),
            debt_profile=DebtProfileV1(
                has_debt=monthly_debt_minimum_total > 0,
                monthly_debt_minimum_total=monthly_debt_minimum_total,
                debts=[],
            ),
            goals_profile=GoalsProfileV1(
                goals_count=len(goals),
                goals_target_total=goals_target_total,
                goals_started_count=goals_started_count,
                goals_with_target_date_count=goals_with_date,
                goals=[],
            ),
            reserve_profile=ReserveProfileV1(
                reserve_current_amount=reserve_current,
                reserve_target_starter=reserve_starter,
                reserve_gap_to_starter=reserve_gap,
            ),
            current_cash_snapshot=CurrentCashSnapshotV1(
                available_now_amount=available_now,
                captured_at=onboarding.updated_at if onboarding else None,
            ),
            data_quality=DataQualityV1(
                notes=[
                    f"payload_top_keys:{','.join(sorted(payload.keys())) if isinstance(payload, dict) else 'none'}",
                    f"onboarding_record_id:{str(onboarding.id) if onboarding else 'none'}",
                    f"income_key:{income_debug.get('income_key') or 'none'}",
                    f"income_raw:{income_debug.get('income_raw')}",
                    f"income_frequency:{income_debug.get('income_frequency') or 'none'}",
                    f"income_scope:{income_debug.get('income_scope') or 'none'}",
                ]
            ),
            derived_totals=DerivedTotalsV1(
                monthly_remaining_before_plan=monthly_remaining,
                cycle_remaining_before_plan=cycle_remaining,
            ),
        )

    async def _load_latest_onboarding(self, db: AsyncSession, user_id):
        result = await db.execute(
            select(OnboardingV2Record)
            .where(OnboardingV2Record.user_id == user_id)
            .order_by(OnboardingV2Record.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _load_goals(self, db: AsyncSession, user_id) -> list[Goal]:
        result = await db.execute(select(Goal).where(Goal.user_id == user_id))
        return list(result.scalars().all())

    def _resolve_cycle_days(self, user: User, payload: dict[str, Any]) -> float:
        candidates = [
            self._find_first_number(payload, ["cycle_days", "cadence_days", "income_cycle_days"]),
            float(user.sweep_interval_days) if user.sweep_interval_days else None,
        ]
        for candidate in candidates:
            if candidate is not None and candidate > 0:
                return float(candidate)
        return 30.0

    def _resolve_monthly_income(self, payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        income_candidates = [
            "S2a_salary_amount",
            "H3_income_profile_min",
            "F7_min_income",
            "M3_min_income",
            "monthly_income_estimate",
            "incomeEstimate",
            "monthly_income_total",
            "income_monthly_total",
            "salary_monthly",
            "income_amount_monthly",
        ]
        frequency_candidates = [
            "S3_frequency",
            "frequency",
            "cadence",
            "salary_frequency",
            "income_frequency",
        ]

        raw_income = None
        income_key = None
        income_scope = None
        raw_frequency = None

        for scope_name, scope_payload in self._income_scopes(payload):
            raw_income, income_key = self._find_first_number_with_key(scope_payload, income_candidates)
            raw_frequency, _ = self._find_first_string_with_key(scope_payload, frequency_candidates)
            if raw_income is not None:
                income_scope = scope_name
                break

        monthly_income = self._to_monthly_amount(raw_income, raw_frequency)
        monthly_income = max(0.0, monthly_income if monthly_income is not None else 0.0)
        return monthly_income, {
            "income_key": income_key,
            "income_raw": raw_income,
            "income_frequency": raw_frequency,
            "income_scope": income_scope,
        }

    def _income_scopes(self, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        scopes: list[tuple[str, dict[str, Any]]] = []
        if isinstance(payload, dict):
            answers = payload.get("answers")
            draft_objects = payload.get("draft_objects")
            if isinstance(answers, dict):
                scopes.append(("answers", answers))
            if isinstance(draft_objects, dict):
                scopes.append(("draft_objects", draft_objects))
            scopes.append(("root", payload))
        return scopes

    def _resolve_monthly_essentials(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(
                payload,
                [
                    "monthly_essential_total",
                    "essential_monthly_total",
                    "core_expenses_monthly",
                ],
                default=0.0,
            ),
        )

    def _resolve_monthly_total_expenses(self, payload: dict[str, Any], fallback: float) -> float:
        resolved = self._find_first_number(
            payload,
            [
                "monthly_expense_total_all",
                "monthly_expenses_total",
                "expenses_monthly_total",
                "fixed_expenses_total_monthly",
            ],
            default=fallback,
        )
        return max(0.0, resolved)

    def _resolve_monthly_sinking(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(
                payload,
                ["monthly_sinking_obligations_total", "sinking_monthly_total", "annualized_monthly_total"],
                default=0.0,
            ),
        )

    def _resolve_monthly_debt_minimum(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(
                payload,
                [
                    "monthly_debt_minimum_total",
                    "debt_minimum_monthly_total",
                    "debt_monthly_payment_total",
                    "debt_payment_monthly",
                ],
                default=0.0,
            ),
        )

    def _resolve_goals_target_total(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(payload, ["goals_target_total", "goals_total_target"], default=0.0),
        )

    def _resolve_reserve_current(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(payload, ["reserve_current_amount", "current_reserve_amount"], default=0.0),
        )

    def _resolve_reserve_starter_target(self, payload: dict[str, Any], monthly_essentials: float) -> float:
        resolved = self._find_first_number(
            payload,
            ["reserve_target_starter", "starter_reserve_target"],
            default=None,
        )
        if resolved is not None:
            return max(0.0, resolved)
        return max(0.0, monthly_essentials)

    def _resolve_available_now(self, payload: dict[str, Any]) -> float:
        return max(
            0.0,
            self._find_first_number(
                payload,
                ["available_now_amount", "current_cash", "current_cash_amount", "cash_available_now"],
                default=0.0,
            ),
        )

    def _monthly_to_cycle(self, monthly_amount: float, cycle_days: float) -> float:
        safe_monthly = max(0.0, float(monthly_amount))
        safe_cycle_days = float(cycle_days) if cycle_days and cycle_days > 0 else 30.0
        return safe_monthly * (safe_cycle_days / 30.0)

    def _to_monthly_amount(self, amount: float | None, frequency: str | None) -> float | None:
        if amount is None:
            return None
        safe_amount = max(0.0, float(amount))
        freq = (frequency or "").strip().lower()
        if not freq:
            return safe_amount
        if freq in {"monthly", "month", "mois", "mensuel"}:
            return safe_amount
        if freq in {"weekly", "week", "hebdo", "hebdomadaire"}:
            return safe_amount * 4.0
        if freq in {"biweekly", "fortnight", "quinzaine", "15d", "15_days"}:
            return safe_amount * 2.0
        if freq in {"quarterly", "quarter", "trimestriel"}:
            return safe_amount / 3.0
        if freq in {"annual", "annually", "yearly", "year", "annuel"}:
            return safe_amount / 12.0
        return safe_amount

    def _find_first_number(self, payload: dict[str, Any], keys: Iterable[str], default: float | None = None) -> float | None:
        for key in keys:
            value = self._find_in_tree(payload, key)
            parsed = self._safe_number(value)
            if parsed is not None:
                return parsed
        return default

    def _find_first_number_with_key(self, payload: dict[str, Any], keys: Iterable[str]) -> tuple[float | None, str | None]:
        for key in keys:
            value = self._find_in_tree(payload, key)
            parsed = self._safe_number(value)
            if parsed is not None:
                return parsed, key
        return None, None

    def _find_first_string_with_key(self, payload: dict[str, Any], keys: Iterable[str]) -> tuple[str | None, str | None]:
        for key in keys:
            value = self._find_in_tree(payload, key)
            if isinstance(value, str) and value.strip():
                return value.strip(), key
        return None, None

    def _find_in_tree(self, obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for value in obj.values():
                found = self._find_in_tree(value, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = self._find_in_tree(value, key)
                if found is not None:
                    return found
        return None

    def _safe_number(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float, Decimal)):
            return float(value)
        if isinstance(value, str):
            raw = value.strip().replace(" ", "").replace("MAD", "")
            raw = raw.replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                return None
        return None
