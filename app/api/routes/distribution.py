from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha1
from collections import defaultdict
import json
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    DistributionItem,
    DistributionSavedConfig,
    DistributionRule,
    Envelope,
    EnvelopePeriod,
    Goal,
    OnboardingV2Record,
    Transaction,
    TransactionType,
    User,
    Sweep,
)
from app.schemas.distribution import (
    DistributionApplyOut,
    DistributionApplyRequest,
    DistributionActiveConfigIn,
    DistributionConfigIn,
    DistributionConfigOut,
    DistributionConfigItemOut,
    DistributionOnboardingStatusIn,
    DistributionOnboardingStatusOut,
    DistributionSavedConfigOut,
    DistributionSavedConfigUpsertIn,
    DistributionSavedRowIn,
    DistributionSavedRowOut,
    DistributionRebalancePreviewIn,
    DistributionRebalancePreviewOut,
    DistributionApplyNextCycleIn,
    DistributionRevertBaselineIn,
    DistributionSimulateOut,
    DistributionSimulateRequest,
)
from app.schemas.distribution_rule import (
    DistributionRuleCreate,
    DistributionRuleOut,
    DistributionRuleReorderItem,
    DistributionRuleUpdate,
)
from app.services.distribution_engine import (
    DistributionContext,
    apply_distribution_plan,
    build_distribution_plan,
)
from app.services.distribution_effective_rules import get_effective_distribution_rules
from app.services.balances import compute_period_balance
from app.services.distribution_name_normalization import (
    distribution_name_equivalent_key,
)
from app.services.onboarding_v2_canonical import compute_canonical_apply_state_backend
from app.services.onboarding_v2_record_state import normalize_record_payload_for_response
from app.services.periods import period_bounds
from app.services.sweep_context import resolve_user_sweep_anchor_date
from app.services.transactions import resolve_cash_envelope

router = APIRouter(prefix="/distribution")


async def _lock_user_distribution_row(
    db: AsyncSession,
    current_user: User,
) -> None:
    # Serialize per-user saved-config writes to avoid version collisions under concurrency.
    await db.execute(
        select(User.id)
        .where(User.id == current_user.id)
        .with_for_update()
    )


def _normalize_rule_mode(mode: str) -> str:
    if mode in {"fixed", "fixed_per_period"}:
        return "fixed_per_period"
    if mode in {"percent", "percent_of_income"}:
        return "percent_of_income"
    raise HTTPException(status_code=400, detail="invalid rule mode")


def _validate_rule_payload(
    mode: str, amount: Optional[Decimal], percent: Optional[Decimal]
) -> None:
    if mode == "fixed_per_period":
        if amount is None or amount <= 0:
            raise HTTPException(status_code=400, detail="fixed requires amount")
        if percent is not None:
            raise HTTPException(status_code=400, detail="fixed cannot include percent")
    elif mode == "percent_of_income":
        if percent is None or percent <= 0:
            raise HTTPException(status_code=400, detail="percent requires value")
        if amount is not None:
            raise HTTPException(status_code=400, detail="percent cannot include amount")
    else:
        raise HTTPException(status_code=400, detail="invalid rule mode")


def _row_signature_payload(rows: list[DistributionSavedRowIn]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "target_type": row.target_type,
                "target_id": str(row.target_id),
                "mode": row.mode,
                "enabled": bool(row.enabled),
                "fixed_amount": str(row.fixed_amount) if row.fixed_amount is not None else None,
                "percent": str(row.percent) if row.percent is not None else None,
                "rank": int(row.rank),
            }
        )
    normalized.sort(key=lambda item: f"{item['target_type']}:{item['target_id']}")
    return normalized


def _build_saved_config_signature(
    *,
    rows: list[DistributionSavedRowIn],
    auto_enabled: bool,
) -> str:
    payload = {
        "auto_enabled": bool(auto_enabled),
        "rows": _row_signature_payload(rows),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha1(raw.encode("utf-8")).hexdigest()


def _serialize_saved_rows(rows: list[DistributionSavedRowIn]) -> list[dict[str, Any]]:
    # Ensure DB-safe primitives for JSONB (UUID/Decimal -> string/number) across runtimes.
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append(json.loads(row.model_dump_json()))
    return serialized


def _validate_saved_rows(rows: list[DistributionSavedRowIn]) -> None:
    for row in rows:
        if row.mode == "none":
            continue
        if row.mode == "fixed":
            if row.fixed_amount is None:
                raise HTTPException(status_code=400, detail="fixed row requires fixed_amount")
            if row.percent is not None:
                raise HTTPException(status_code=400, detail="fixed row cannot include percent")
            continue
        if row.mode == "percent":
            if row.percent is None:
                raise HTTPException(status_code=400, detail="percent row requires percent")
            if row.fixed_amount is not None:
                raise HTTPException(status_code=400, detail="percent row cannot include fixed_amount")
            continue


def _to_saved_config_out(
    config: DistributionSavedConfig,
    *,
    envelope_name_by_id: dict[UUID, str],
    goal_name_by_id: dict[UUID, str],
) -> DistributionSavedConfigOut:
    rows_out: list[DistributionSavedRowOut] = []
    for item in config.rows if isinstance(config.rows, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            row = DistributionSavedRowOut.model_validate(item)
        except Exception:
            continue
        if row.target_type == "envelope":
            row.name = envelope_name_by_id.get(row.target_id)
        else:
            row.name = goal_name_by_id.get(row.target_id)
        rows_out.append(row)
    return DistributionSavedConfigOut(
        id=config.id,
        name=config.name,
        auto_enabled=config.auto_enabled,
        percent_mode="ranked" if config.percent_mode == "ranked" else "equal",
        rows=rows_out,
        scope_hash=config.scope_hash,
        signature=config.signature,
        source="onboarding_initial" if config.source == "onboarding_initial" else "post_onboarding_adjustment",
        version=int(config.version or 1),
        effective_from_period_start=config.effective_from_period_start,
        is_active=config.is_active,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


async def _active_saved_config_for_date(
    db: AsyncSession,
    current_user: User,
    period_start: date,
) -> DistributionSavedConfig | None:
    result = await db.execute(
        select(DistributionSavedConfig)
        .where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
        .order_by(DistributionSavedConfig.version.desc(), DistributionSavedConfig.updated_at.desc())
    )
    active = result.scalar_one_or_none()
    if active is None:
        return None
    effective = active.effective_from_period_start
    if effective is not None and effective > period_start:
        return None
    return active


def _extract_mode_amounts(rows: list[DistributionSavedRowIn]) -> tuple[Decimal, Decimal, Decimal]:
    debt = Decimal("0.00")
    goals = Decimal("0.00")
    morona = Decimal("0.00")
    for row in rows:
        if not row.enabled:
            continue
        key = f"{row.target_type}:{row.target_id}".lower()
        amount = Decimal(str(row.fixed_amount or 0)) if row.mode == "fixed" else Decimal("0.00")
        if "debt" in key or "dette" in key:
            debt += amount
        elif row.target_type == "goal":
            goals += amount
        else:
            morona += amount
    return debt, goals, morona


def _rebalance_from_cuts(total: Decimal, cut1_pct: Decimal, cut2_pct: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    debt = (total * (cut1_pct / Decimal("100"))).quantize(Decimal("0.01"))
    goals = (total * ((cut2_pct - cut1_pct) / Decimal("100"))).quantize(Decimal("0.01"))
    morona = (total - debt - goals).quantize(Decimal("0.01"))
    return max(Decimal("0.00"), debt), max(Decimal("0.00"), goals), max(Decimal("0.00"), morona)


async def _load_effective_fixed_rows_for_rebalance(
    db: AsyncSession,
    current_user: User,
    *,
    fallback_rows: list[DistributionSavedRowIn],
) -> list[DistributionSavedRowIn]:
    rules_result = await db.execute(
        select(DistributionRule).where(
            DistributionRule.user_id == current_user.id,
            DistributionRule.enabled.is_(True),
            DistributionRule.mode.in_(("fixed", "fixed_per_period")),
            DistributionRule.amount.is_not(None),
        ).order_by(DistributionRule.rank.asc(), DistributionRule.created_at.asc())
    )
    rules = list(rules_result.scalars().all())
    if rules:
        rows: list[DistributionSavedRowIn] = []
        for rule in rules:
            amount = Decimal(str(rule.amount or 0)).quantize(Decimal("0.01"))
            if amount <= 0:
                continue
            rows.append(
                DistributionSavedRowIn(
                    target_type=rule.target_type,
                    target_id=rule.target_id,
                    mode="fixed",
                    enabled=bool(rule.enabled),
                    fixed_amount=amount,
                    percent=None,
                    rank=max(1, int(rule.rank or 1)),
                )
            )
        if rows:
            return rows

    return [
        row
        for row in fallback_rows
        if row.enabled and row.mode == "fixed" and (row.fixed_amount or Decimal("0.00")) > 0
    ]


async def _is_sweep_due_now(db: AsyncSession, current_user: User, today: date) -> bool:
    anchor = await resolve_user_sweep_anchor_date(db, current_user)
    current_start, current_end = period_bounds(anchor, today, current_user.sweep_interval_days)
    if current_end > today:
        return False
    income_count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == current_user.id,
            Transaction.occurred_on >= current_start,
            Transaction.occurred_on < current_end,
            Transaction.type == TransactionType.INCOME,
        )
    )
    income_declared = int(income_count_result.scalar_one()) > 0
    if not income_declared:
        return False
    sweep_result = await db.execute(
        select(func.count(Sweep.id)).where(
            Sweep.user_id == current_user.id,
            Sweep.swept_on == current_end,
        )
    )
    return int(sweep_result.scalar_one()) == 0


async def _get_target_name_maps(
    db: AsyncSession, current_user: User
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(False),
        )
    )
    goals_result = await db.execute(select(Goal).where(Goal.user_id == current_user.id))
    envelope_name_by_id = {env.id: env.name for env in envelopes_result.scalars().all()}
    goal_name_by_id = {goal.id: goal.name for goal in goals_result.scalars().all()}
    return envelope_name_by_id, goal_name_by_id


async def _apply_saved_rows_to_distribution_rules(
    db: AsyncSession,
    current_user: User,
    *,
    rows: list[DistributionSavedRowIn],
) -> None:
    def _safe_decimal(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception:
            return None

    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(False),
        )
    )
    envelopes = list(envelopes_result.scalars().all())
    envelope_by_name = {env.name.strip().casefold(): env for env in envelopes}
    envelope_by_key = {
        distribution_name_equivalent_key(env.name): env
        for env in envelopes
        if distribution_name_equivalent_key(env.name)
    }
    default_savings_envelope = next(
        (env for env in envelopes if bool(env.is_default_savings)),
        None,
    )

    goals_result = await db.execute(select(Goal).where(Goal.user_id == current_user.id))
    goals = list(goals_result.scalars().all())
    goal_by_key = {
        distribution_name_equivalent_key(goal.name): goal
        for goal in goals
        if distribution_name_equivalent_key(goal.name)
    }

    baseline_fixed_rules: list[DistributionRule] = []
    latest_record_result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == current_user.id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(1)
    )
    latest_record = latest_record_result.scalar_one_or_none()
    payload = latest_record.payload if latest_record and isinstance(latest_record.payload, dict) else {}
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}

    if answers:
        canonical_state = compute_canonical_apply_state_backend(answers)
        layer_priority = {
            "protected": 10,
            "planned_future_obligation": 40,
            "scheduled": 70,
        }
        aggregated: dict[str, dict[str, Any]] = {}
        for item in canonical_state.cycle_normalized_expenses_v1:
            envelope_name = str(item.get("envelope") or "").strip()
            item_label = str(item.get("label") or "").strip()
            per_cycle_amount = _safe_decimal(item.get("per_cycle_amount"))
            priority_layer = str(item.get("priority_layer") or "scheduled").strip() or "scheduled"
            if per_cycle_amount is None:
                continue
            resolved_envelope_name = envelope_name
            if envelope_name:
                key = distribution_name_equivalent_key(envelope_name)
                resolved = envelope_by_key.get(key) if key else None
                if resolved is not None:
                    resolved_envelope_name = resolved.name
                elif item_label:
                    label_key = distribution_name_equivalent_key(item_label)
                    resolved_by_label = envelope_by_key.get(label_key) if label_key else None
                    if resolved_by_label is not None:
                        resolved_envelope_name = resolved_by_label.name
            elif item_label:
                label_key = distribution_name_equivalent_key(item_label)
                resolved_by_label = envelope_by_key.get(label_key) if label_key else None
                if resolved_by_label is not None:
                    resolved_envelope_name = resolved_by_label.name
            if not resolved_envelope_name:
                continue
            bucket = aggregated.setdefault(
                resolved_envelope_name,
                {
                    "amount": Decimal("0.00"),
                    "priority": layer_priority.get(priority_layer, 70),
                },
            )
            bucket["amount"] += per_cycle_amount
            bucket["priority"] = min(int(bucket["priority"]), layer_priority.get(priority_layer, 70))

        reserve_amount = _safe_decimal(canonical_state.reserve_plan_v1.get("starter_seed_per_cycle"))
        reserve_target_name_raw = str(canonical_state.reserve_plan_v1.get("target_envelope_name") or "").strip()
        reserve_target_envelope = None
        if reserve_target_name_raw:
            reserve_target_envelope = envelope_by_name.get(reserve_target_name_raw.casefold())
            if reserve_target_envelope is None:
                reserve_target_envelope = envelope_by_key.get(
                    distribution_name_equivalent_key(reserve_target_name_raw)
                )
        if reserve_target_envelope is None:
            reserve_target_envelope = default_savings_envelope
        if reserve_amount is not None and reserve_amount > 0 and reserve_target_envelope is not None:
            bucket = aggregated.setdefault(
                reserve_target_envelope.name,
                {"amount": Decimal("0.00"), "priority": 85},
            )
            bucket["amount"] += reserve_amount
            bucket["priority"] = min(int(bucket["priority"]), 85)

        suggested_extra = _safe_decimal(canonical_state.debt_plan_v2.get("suggested_extra_per_cycle"))
        debt_candidates = []
        for item in canonical_state.debts:
            final_name = str(item.get("envelope_name") or item.get("name") or "").strip()
            original_name = str(item.get("name") or "").strip()
            if final_name:
                debt_candidates.append((final_name, original_name))
        if suggested_extra is not None and suggested_extra > 0 and debt_candidates:
            if canonical_state.debt_posture == "focus":
                focus_name = str(canonical_state.debt_plan_v2.get("focus_debt_name") or "").strip()
                chosen_name = None
                if focus_name:
                    for final_name, original_name in debt_candidates:
                        haystack = f"{final_name} {original_name}".casefold()
                        if focus_name.casefold() in haystack or haystack in focus_name.casefold():
                            chosen_name = final_name
                            break
                chosen_name = chosen_name or debt_candidates[0][0]
                bucket = aggregated.setdefault(
                    chosen_name,
                    {"amount": Decimal("0.00"), "priority": 95},
                )
                bucket["amount"] += suggested_extra
                bucket["priority"] = min(int(bucket["priority"]), 95)
            elif canonical_state.debt_posture == "balanced":
                split = (suggested_extra / Decimal(str(len(debt_candidates)))).quantize(Decimal("0.01"))
                for final_name, _ in debt_candidates:
                    bucket = aggregated.setdefault(
                        final_name,
                        {"amount": Decimal("0.00"), "priority": 100},
                    )
                    bucket["amount"] += split
                    bucket["priority"] = min(int(bucket["priority"]), 100)

        rank = 1
        for envelope_name, payload_item in sorted(
            aggregated.items(),
            key=lambda item: (int(item[1]["priority"]), item[0].casefold()),
        ):
            key = distribution_name_equivalent_key(envelope_name)
            envelope = envelope_by_key.get(key) if key else None
            if envelope is None:
                continue
            amount = Decimal(payload_item["amount"]).quantize(Decimal("0.01"))
            if amount <= 0:
                continue
            baseline_fixed_rules.append(
                DistributionRule(
                    user_id=current_user.id,
                    target_type="envelope",
                    target_id=envelope.id,
                    mode="fixed_per_period",
                    amount=amount,
                    percent=None,
                    priority=int(payload_item["priority"]),
                    rank=rank,
                    enabled=True,
                    auto_apply_on_income=True,
                )
            )
            rank += 1

        for source in [*canonical_state.goals, *canonical_state.sinking_funds]:
            goal_name = str(source.get("name") or "").strip()
            contribution_amount = _safe_decimal(source.get("contribution_amount"))
            if (
                not goal_name
                or contribution_amount is None
                or contribution_amount <= 0
                or not bool(source.get("auto_contribute"))
            ):
                continue
            key = distribution_name_equivalent_key(goal_name)
            goal = goal_by_key.get(key) if key else None
            if goal is None:
                continue
            baseline_fixed_rules.append(
                DistributionRule(
                    user_id=current_user.id,
                    target_type="goal",
                    target_id=goal.id,
                    mode="fixed_per_period",
                    amount=contribution_amount,
                    percent=None,
                    priority=120 if goal.goal_type == "goal" else 60,
                    rank=rank,
                    enabled=True,
                    auto_apply_on_income=True,
                )
            )
            rank += 1

    active_fixed_targets = {
        (row.target_type, row.target_id)
        for row in rows
        if row.enabled and row.mode == "fixed"
    }
    fixed_to_keep = [
        rule
        for rule in baseline_fixed_rules
        if (rule.target_type, rule.target_id) not in active_fixed_targets
    ]

    await db.execute(delete(DistributionRule).where(DistributionRule.user_id == current_user.id))
    rank = 1
    for rule in fixed_to_keep:
        db.add(
            DistributionRule(
                user_id=current_user.id,
                target_type=rule.target_type,
                target_id=rule.target_id,
                mode="fixed_per_period",
                amount=Decimal(str(rule.amount or 0)).quantize(Decimal("0.01")),
                percent=None,
                priority=rule.priority,
                rank=rank,
                enabled=True,
                auto_apply_on_income=True,
            )
        )
        rank += 1

    for row in sorted(rows, key=lambda item: item.rank):
        if row.mode == "none":
            continue
        db.add(
            DistributionRule(
                user_id=current_user.id,
                target_type=row.target_type,
                target_id=row.target_id,
                mode="fixed_per_period" if row.mode == "fixed" else "percent_of_income",
                amount=row.fixed_amount if row.mode == "fixed" else None,
                percent=row.percent if row.mode == "percent" else None,
                priority=100,
                rank=rank,
                enabled=row.enabled,
                auto_apply_on_income=True,
            )
        )
        rank += 1


async def _current_period_bounds(
    db: AsyncSession, current_user: User, occurred_on: date | None = None
) -> Tuple[date, date, date]:
    today = date.today()
    latest_result = await db.execute(
        select(func.max(Transaction.occurred_on)).where(
            Transaction.user_id == current_user.id
        )
    )
    latest_occurred = latest_result.scalar_one_or_none()
    as_of = occurred_on or latest_occurred or today
    anchor_date = await resolve_user_sweep_anchor_date(db, current_user)
    period_start, period_end = period_bounds(
        anchor_date,
        as_of,
        current_user.sweep_interval_days,
    )
    return as_of, period_start, period_end


async def _cash_balance_for_period(
    db: AsyncSession, current_user: User, period_start: date, period_end: date
) -> Decimal:
    cash = await resolve_cash_envelope(db, current_user.id)
    period_result = await db.execute(
        select(EnvelopePeriod).where(
            EnvelopePeriod.user_id == current_user.id,
            EnvelopePeriod.envelope_id == cash.id,
            EnvelopePeriod.period_start == period_start,
            EnvelopePeriod.period_end == period_end,
        )
    )
    period = period_result.scalar_one_or_none()
    if period is None:
        return Decimal("0.00")
    balance = await compute_period_balance(db, period.id)
    return balance["closing_balance"]


@router.get("/rules", response_model=List[DistributionRuleOut])
async def list_distribution_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[DistributionRuleOut]:
    result = await db.execute(
        select(DistributionRule)
        .where(DistributionRule.user_id == current_user.id)
        .order_by(
            DistributionRule.rank.asc(),
            DistributionRule.created_at.asc(),
        )
    )
    rules = list(result.scalars().all())
    return [
        DistributionRuleOut(
            id=rule.id,
            target_type=rule.target_type,
            target_id=rule.target_id,
            mode=rule.mode,
            amount=rule.amount,
            percent=rule.percent,
            priority=rule.priority,
            rank=rule.rank,
            enabled=rule.enabled,
            auto_apply_on_income=rule.auto_apply_on_income,
            created_at=rule.created_at,
        )
        for rule in rules
    ]


@router.post(
    "/rules",
    response_model=DistributionRuleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_distribution_rule(
    payload: DistributionRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionRuleOut:
    mode = _normalize_rule_mode(payload.mode)
    _validate_rule_payload(mode, payload.amount, payload.percent)

    if payload.target_type == "envelope":
        envelope_result = await db.execute(
            select(Envelope).where(
                Envelope.user_id == current_user.id,
                Envelope.id == payload.target_id,
            )
        )
        if envelope_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="invalid envelope target")
    else:
        goal_result = await db.execute(
            select(Goal).where(
                Goal.user_id == current_user.id,
                Goal.id == payload.target_id,
            )
        )
        if goal_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="invalid goal target")

    rule = DistributionRule(
        user_id=current_user.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        mode=mode,
        amount=payload.amount if mode == "fixed_per_period" else None,
        percent=payload.percent if mode == "percent_of_income" else None,
        priority=payload.priority,
        rank=payload.rank,
        enabled=payload.enabled,
        auto_apply_on_income=payload.auto_apply_on_income,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return DistributionRuleOut(
        id=rule.id,
        target_type=rule.target_type,
        target_id=rule.target_id,
        mode=rule.mode,
        amount=rule.amount,
        percent=rule.percent,
        priority=rule.priority,
        rank=rule.rank,
        enabled=rule.enabled,
        auto_apply_on_income=rule.auto_apply_on_income,
        created_at=rule.created_at,
    )


@router.patch("/rules/{rule_id}", response_model=DistributionRuleOut)
async def update_distribution_rule(
    rule_id: UUID,
    payload: DistributionRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionRuleOut:
    result = await db.execute(
        select(DistributionRule).where(
            DistributionRule.user_id == current_user.id,
            DistributionRule.id == rule_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")

    target_type = payload.target_type or rule.target_type
    target_id = payload.target_id or rule.target_id
    if payload.target_type or payload.target_id:
        if target_type == "envelope":
            envelope_result = await db.execute(
                select(Envelope).where(
                    Envelope.user_id == current_user.id,
                    Envelope.id == target_id,
                )
            )
            if envelope_result.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail="invalid envelope target")
        else:
            goal_result = await db.execute(
                select(Goal).where(
                    Goal.user_id == current_user.id, Goal.id == target_id
                )
            )
            if goal_result.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail="invalid goal target")

    fields_set = payload.model_fields_set
    mode = _normalize_rule_mode(payload.mode) if payload.mode else rule.mode
    amount = payload.amount if "amount" in fields_set else rule.amount
    percent = payload.percent if "percent" in fields_set else rule.percent
    if mode == "fixed_per_period":
        percent = None
    if mode == "percent_of_income":
        amount = None
    _validate_rule_payload(mode, amount, percent)

    rule.target_type = target_type
    rule.target_id = target_id
    rule.mode = mode
    rule.amount = amount if mode == "fixed_per_period" else None
    rule.percent = percent if mode == "percent_of_income" else None
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.rank is not None:
        rule.rank = payload.rank
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    if payload.auto_apply_on_income is not None:
        rule.auto_apply_on_income = payload.auto_apply_on_income

    await db.commit()
    await db.refresh(rule)
    return DistributionRuleOut(
        id=rule.id,
        target_type=rule.target_type,
        target_id=rule.target_id,
        mode=rule.mode,
        amount=rule.amount,
        percent=rule.percent,
        priority=rule.priority,
        rank=rule.rank,
        enabled=rule.enabled,
        auto_apply_on_income=rule.auto_apply_on_income,
        created_at=rule.created_at,
    )


@router.put("/rules/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_distribution_rules(
    payload: List[DistributionRuleReorderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if not payload:
        return None

    ids = [item.id for item in payload]
    existing_result = await db.execute(
        select(DistributionRule).where(
            DistributionRule.user_id == current_user.id,
            DistributionRule.id.in_(ids),
        )
    )
    rules = {rule.id: rule for rule in existing_result.scalars().all()}
    for item in payload:
        rule = rules.get(item.id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        rule.rank = item.rank

    await db.commit()
    return None


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_distribution_rule(
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(DistributionRule).where(
            DistributionRule.user_id == current_user.id,
            DistributionRule.id == rule_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    await db.delete(rule)
    await db.commit()
    return None


@router.get("/configs", response_model=List[DistributionSavedConfigOut])
async def list_saved_distribution_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[DistributionSavedConfigOut]:
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    result = await db.execute(
        select(DistributionSavedConfig)
        .where(DistributionSavedConfig.user_id == current_user.id)
        .order_by(
            DistributionSavedConfig.updated_at.desc(),
            DistributionSavedConfig.created_at.desc(),
        )
    )
    configs = list(result.scalars().all())
    return [
        _to_saved_config_out(
            config,
            envelope_name_by_id=envelope_name_by_id,
            goal_name_by_id=goal_name_by_id,
        )
        for config in configs
    ]


@router.post("/configs", response_model=DistributionSavedConfigOut)
async def upsert_saved_distribution_config(
    payload: DistributionSavedConfigUpsertIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSavedConfigOut:
    _validate_saved_rows(payload.rows)
    signature = _build_saved_config_signature(
        rows=payload.rows,
        auto_enabled=payload.auto_enabled,
    )

    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    valid_envelope_ids = set(envelope_name_by_id.keys())
    valid_goal_ids = set(goal_name_by_id.keys())
    for row in payload.rows:
        if row.target_type == "envelope" and row.target_id not in valid_envelope_ids:
            raise HTTPException(status_code=400, detail="invalid envelope row target")
        if row.target_type == "goal" and row.target_id not in valid_goal_ids:
            raise HTTPException(status_code=400, detail="invalid goal row target")

    existing_same_signature_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.signature == signature,
        ).order_by(DistributionSavedConfig.updated_at.desc()).limit(1)
    )
    existing_same_signature = existing_same_signature_result.scalar_one_or_none()

    config: DistributionSavedConfig | None = None
    if payload.id is not None:
        existing_by_id_result = await db.execute(
            select(DistributionSavedConfig).where(
                DistributionSavedConfig.user_id == current_user.id,
                DistributionSavedConfig.id == payload.id,
            )
        )
        config = existing_by_id_result.scalar_one_or_none()

    if config is None and existing_same_signature is not None:
        config = existing_same_signature

    try:
        if config is None:
            await _lock_user_distribution_row(db, current_user)
            max_version_result = await db.execute(
                select(func.coalesce(func.max(DistributionSavedConfig.version), 0)).where(
                    DistributionSavedConfig.user_id == current_user.id
                )
            )
            next_version = int(max_version_result.scalar_one() or 0) + 1
            source = "onboarding_initial" if next_version == 1 else "post_onboarding_adjustment"
            # Deactivate currently active config(s) first to satisfy the partial unique index
            # on (user_id) where is_active=true before inserting the new active row.
            await db.execute(
                DistributionSavedConfig.__table__.update()
                .where(DistributionSavedConfig.user_id == current_user.id)
                .values(is_active=False)
            )
            create_kwargs: dict[str, Any] = {}
            if payload.id is not None:
                create_kwargs["id"] = payload.id
            config = DistributionSavedConfig(
                user_id=current_user.id,
                name=payload.name.strip(),
                rows=_serialize_saved_rows(payload.rows),
                signature=signature,
                percent_mode=payload.percent_mode,
                auto_enabled=payload.auto_enabled,
                scope_hash=payload.scope_hash,
                source=source,
                version=next_version,
                is_active=True,
                **create_kwargs,
            )
            db.add(config)
        else:
            config.name = payload.name.strip()
            config.rows = _serialize_saved_rows(payload.rows)
            config.signature = signature
            config.percent_mode = payload.percent_mode
            config.auto_enabled = payload.auto_enabled
            config.scope_hash = payload.scope_hash
            # Important: deactivate other active configs before marking this one active.
            # If we flip `config.is_active=True` first, SQLAlchemy can autoflush the row
            # before the UPDATE below runs, which can violate the partial unique index
            # (user_id where is_active=true) and raise a 409 conflict.
            await db.execute(
                DistributionSavedConfig.__table__.update()
                .where(
                    DistributionSavedConfig.user_id == current_user.id,
                    DistributionSavedConfig.id != config.id,
                )
                .values(is_active=False)
            )
            config.is_active = True

        current_user.auto_distribution_enabled = payload.auto_enabled
        await _apply_saved_rows_to_distribution_rules(db, current_user, rows=payload.rows)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="تعذّر حفظ إعداد التوزيع الآن، عاود المحاولة.",
        ) from exc

    await db.refresh(config)
    return _to_saved_config_out(
        config,
        envelope_name_by_id=envelope_name_by_id,
        goal_name_by_id=goal_name_by_id,
    )


@router.put("/configs/active", response_model=DistributionSavedConfigOut)
async def set_active_saved_distribution_config(
    payload: DistributionActiveConfigIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSavedConfigOut:
    result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id == payload.config_id,
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="saved config not found")

    await db.execute(
        DistributionSavedConfig.__table__.update()
        .where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id != config.id,
        )
        .values(is_active=False)
    )
    config.is_active = True
    current_user.auto_distribution_enabled = config.auto_enabled
    rows = []
    if isinstance(config.rows, list):
        for item in config.rows:
            if not isinstance(item, dict):
                continue
            try:
                rows.append(DistributionSavedRowIn.model_validate(item))
            except Exception:
                continue
    await _apply_saved_rows_to_distribution_rules(db, current_user, rows=rows)
    await db.commit()
    await db.refresh(config)
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    return _to_saved_config_out(
        config,
        envelope_name_by_id=envelope_name_by_id,
        goal_name_by_id=goal_name_by_id,
    )


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_distribution_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id == config_id,
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="saved config not found")

    was_active = bool(config.is_active)
    await db.delete(config)

    if was_active:
        replacement_result = await db.execute(
            select(DistributionSavedConfig)
            .where(DistributionSavedConfig.user_id == current_user.id)
            .order_by(
                DistributionSavedConfig.updated_at.desc(),
                DistributionSavedConfig.created_at.desc(),
            )
            .limit(1)
        )
        replacement = replacement_result.scalar_one_or_none()
        if replacement is None:
            # Keep baseline fixed rules (onboarding commitments) and clear only config-driven rows.
            await _apply_saved_rows_to_distribution_rules(db, current_user, rows=[])
            current_user.auto_distribution_enabled = False
        else:
            await db.execute(
                DistributionSavedConfig.__table__.update()
                .where(
                    DistributionSavedConfig.user_id == current_user.id,
                    DistributionSavedConfig.id != replacement.id,
                )
                .values(is_active=False)
            )
            replacement.is_active = True
            current_user.auto_distribution_enabled = replacement.auto_enabled
            rows: list[DistributionSavedRowIn] = []
            if isinstance(replacement.rows, list):
                for item in replacement.rows:
                    if not isinstance(item, dict):
                        continue
                    try:
                        rows.append(DistributionSavedRowIn.model_validate(item))
                    except Exception:
                        continue
            await _apply_saved_rows_to_distribution_rules(db, current_user, rows=rows)

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/configs/active", response_model=DistributionSavedConfigOut)
async def get_active_saved_distribution_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSavedConfigOut:
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    result = await db.execute(
        select(DistributionSavedConfig)
        .where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
        .order_by(DistributionSavedConfig.version.desc(), DistributionSavedConfig.updated_at.desc())
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="active config not found")
    return _to_saved_config_out(
        config,
        envelope_name_by_id=envelope_name_by_id,
        goal_name_by_id=goal_name_by_id,
    )


@router.post("/configs/{config_id}/preview-rebalance", response_model=DistributionRebalancePreviewOut)
async def preview_saved_distribution_rebalance(
    config_id: UUID,
    payload: DistributionRebalancePreviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionRebalancePreviewOut:
    if payload.cut2_pct < payload.cut1_pct:
        raise HTTPException(status_code=400, detail="cut2_pct must be >= cut1_pct")
    result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id == config_id,
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="saved config not found")
    rows: list[DistributionSavedRowIn] = []
    for item in config.rows if isinstance(config.rows, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(DistributionSavedRowIn.model_validate(item))
        except Exception:
            continue
    fixed_rows = await _load_effective_fixed_rows_for_rebalance(
        db,
        current_user,
        fallback_rows=rows,
    )
    active_debt, active_goals, active_morona = _extract_mode_amounts(fixed_rows)
    total = active_debt + active_goals + active_morona
    if total <= 0:
        raise HTTPException(status_code=409, detail="no fixed rows to rebalance")
    debt_amount, goals_amount, morona_amount = _rebalance_from_cuts(
        total,
        Decimal(str(payload.cut1_pct)),
        Decimal(str(payload.cut2_pct)),
    )
    return DistributionRebalancePreviewOut(
        debt_amount=debt_amount,
        goals_amount=goals_amount,
        morona_amount=morona_amount,
        delta_vs_active={
            "debt": (debt_amount - active_debt).quantize(Decimal("0.01")),
            "goals": (goals_amount - active_goals).quantize(Decimal("0.01")),
            "morona": (morona_amount - active_morona).quantize(Decimal("0.01")),
        },
    )


@router.post("/configs/{config_id}/apply-next-cycle", response_model=DistributionSavedConfigOut)
async def apply_saved_distribution_next_cycle(
    config_id: UUID,
    payload: DistributionApplyNextCycleIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSavedConfigOut:
    if payload.cut2_pct < payload.cut1_pct:
        raise HTTPException(status_code=400, detail="cut2_pct must be >= cut1_pct")
    if payload.effective_from_period_start <= date.today():
        raise HTTPException(status_code=400, detail="effective_from_period_start must be in the future")
    if await _is_sweep_due_now(db, current_user, date.today()):
        raise HTTPException(status_code=409, detail="Sweep due: apply is locked until sweep is completed.")
    result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id == config_id,
        )
    )
    base_config = result.scalar_one_or_none()
    if base_config is None:
        raise HTTPException(status_code=404, detail="saved config not found")

    # Idempotence: same user/base/effective/cuts => return existing version.
    idem_signature = sha1(
        json.dumps(
            {
                "base_config_id": str(base_config.id),
                "cut1_pct": str(payload.cut1_pct),
                "cut2_pct": str(payload.cut2_pct),
                "effective_from_period_start": payload.effective_from_period_start.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.signature == idem_signature,
            DistributionSavedConfig.effective_from_period_start == payload.effective_from_period_start,
        )
    )
    existing = existing_result.scalar_one_or_none()
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    if existing is not None:
        return _to_saved_config_out(
            existing,
            envelope_name_by_id=envelope_name_by_id,
            goal_name_by_id=goal_name_by_id,
        )

    base_rows: list[DistributionSavedRowIn] = []
    for item in base_config.rows if isinstance(base_config.rows, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            base_rows.append(DistributionSavedRowIn.model_validate(item))
        except Exception:
            continue
    fixed_rows = await _load_effective_fixed_rows_for_rebalance(
        db,
        current_user,
        fallback_rows=base_rows,
    )
    # Rebalance only fixed rows proportionally by bucket to avoid breaking configured structure.
    debt_rows: list[DistributionSavedRowIn] = []
    goal_rows: list[DistributionSavedRowIn] = []
    morona_rows: list[DistributionSavedRowIn] = []
    for row in fixed_rows:
        if not row.enabled or row.mode != "fixed":
            continue
        if row.target_type == "goal":
            goal_rows.append(row)
        else:
            name = envelope_name_by_id.get(row.target_id, "")
            normalized = name.casefold()
            if "debt" in normalized or "dette" in normalized or "قرض" in normalized or "دين" in normalized:
                debt_rows.append(row)
            else:
                morona_rows.append(row)
    active_debt = sum((Decimal(str(r.fixed_amount or 0)) for r in debt_rows), Decimal("0.00"))
    active_goals = sum((Decimal(str(r.fixed_amount or 0)) for r in goal_rows), Decimal("0.00"))
    active_morona = sum((Decimal(str(r.fixed_amount or 0)) for r in morona_rows), Decimal("0.00"))
    total = active_debt + active_goals + active_morona
    if total <= 0:
        raise HTTPException(status_code=409, detail="no fixed rows to rebalance")
    target_debt, target_goals, target_morona = _rebalance_from_cuts(
        total, Decimal(str(payload.cut1_pct)), Decimal(str(payload.cut2_pct))
    )

    def _scale_bucket(bucket: list[DistributionSavedRowIn], bucket_total: Decimal, target_total: Decimal) -> dict[UUID, Decimal]:
        if not bucket:
            return {}
        if bucket_total <= 0:
            even = (target_total / Decimal(str(len(bucket)))).quantize(Decimal("0.01"))
            return {r.target_id: even for r in bucket}
        scaled: dict[UUID, Decimal] = {}
        running = Decimal("0.00")
        for idx, row in enumerate(bucket):
            if idx == len(bucket) - 1:
                amount = (target_total - running).quantize(Decimal("0.01"))
            else:
                base = Decimal(str(row.fixed_amount or 0))
                amount = (target_total * (base / bucket_total)).quantize(Decimal("0.01"))
                running += amount
            scaled[row.target_id] = max(Decimal("0.00"), amount)
        return scaled

    scaled_debt = _scale_bucket(debt_rows, active_debt, target_debt)
    scaled_goals = _scale_bucket(goal_rows, active_goals, target_goals)
    scaled_morona = _scale_bucket(morona_rows, active_morona, target_morona)
    adjusted_fixed_by_target: dict[tuple[str, UUID], Decimal] = {}
    for row in fixed_rows:
        if not row.enabled or row.mode != "fixed":
            continue
        if row.target_type == "goal":
            next_amount = scaled_goals.get(row.target_id, row.fixed_amount or Decimal("0.00"))
        else:
            name = envelope_name_by_id.get(row.target_id, "")
            normalized = name.casefold()
            if "debt" in normalized or "dette" in normalized or "قرض" in normalized or "دين" in normalized:
                next_amount = scaled_debt.get(row.target_id, row.fixed_amount or Decimal("0.00"))
            else:
                next_amount = scaled_morona.get(row.target_id, row.fixed_amount or Decimal("0.00"))
        adjusted_fixed_by_target[(row.target_type, row.target_id)] = max(
            Decimal("0.01"),
            Decimal(str(next_amount)).quantize(Decimal("0.01")),
        )

    adjusted_rows: list[DistributionSavedRowIn] = []
    seen_fixed_targets: set[tuple[str, UUID]] = set()
    for row in base_rows:
        if row.mode != "fixed" or not row.enabled:
            adjusted_rows.append(row)
            continue
        key = (row.target_type, row.target_id)
        seen_fixed_targets.add(key)
        next_amount = adjusted_fixed_by_target.get(key, row.fixed_amount or Decimal("0.00"))
        adjusted_rows.append(
            DistributionSavedRowIn(
                target_type=row.target_type,
                target_id=row.target_id,
                mode=row.mode,
                enabled=row.enabled,
                fixed_amount=max(Decimal("0.01"), Decimal(str(next_amount))).quantize(Decimal("0.01")),
                percent=row.percent,
                rank=row.rank,
            )
        )
    next_rank = max((row.rank for row in adjusted_rows), default=0) + 1
    for key, amount in adjusted_fixed_by_target.items():
        if key in seen_fixed_targets:
            continue
        target_type, target_id = key
        adjusted_rows.append(
            DistributionSavedRowIn(
                target_type=target_type,
                target_id=target_id,
                mode="fixed",
                enabled=True,
                fixed_amount=amount,
                percent=None,
                rank=next_rank,
            )
        )
        next_rank += 1

    await _lock_user_distribution_row(db, current_user)
    max_version_result = await db.execute(
        select(func.coalesce(func.max(DistributionSavedConfig.version), 0)).where(
            DistributionSavedConfig.user_id == current_user.id
        )
    )
    next_version = int(max_version_result.scalar_one() or 0) + 1
    await db.execute(
        DistributionSavedConfig.__table__.update()
        .where(DistributionSavedConfig.user_id == current_user.id)
        .values(is_active=False)
    )
    next_config = DistributionSavedConfig(
        user_id=current_user.id,
        name=f"{base_config.name} v{next_version}",
        rows=_serialize_saved_rows(adjusted_rows),
        signature=idem_signature,
        percent_mode=base_config.percent_mode,
        auto_enabled=base_config.auto_enabled,
        scope_hash=base_config.scope_hash,
        source="post_onboarding_adjustment",
        version=next_version,
        effective_from_period_start=payload.effective_from_period_start,
        is_active=True,
    )
    db.add(next_config)
    await db.flush()
    await _apply_saved_rows_to_distribution_rules(db, current_user, rows=adjusted_rows)
    await db.commit()
    await db.refresh(next_config)
    return _to_saved_config_out(
        next_config,
        envelope_name_by_id=envelope_name_by_id,
        goal_name_by_id=goal_name_by_id,
    )


@router.post("/configs/{config_id}/revert-onboarding-baseline", response_model=DistributionSavedConfigOut)
async def revert_saved_distribution_to_onboarding_baseline(
    config_id: UUID,
    payload: DistributionRevertBaselineIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSavedConfigOut:
    if payload.effective_from_period_start <= date.today():
        raise HTTPException(status_code=400, detail="effective_from_period_start must be in the future")
    requested_config_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.id == config_id,
        )
    )
    requested_config = requested_config_result.scalar_one_or_none()
    if requested_config is None:
        raise HTTPException(status_code=404, detail="saved config not found")

    baseline_result = await db.execute(
        select(DistributionSavedConfig)
        .where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.source == "onboarding_initial",
        )
        .order_by(DistributionSavedConfig.version.asc(), DistributionSavedConfig.created_at.asc())
        .limit(1)
    )
    baseline = baseline_result.scalar_one_or_none()
    if baseline is None:
        fallback_result = await db.execute(
            select(DistributionSavedConfig)
            .where(DistributionSavedConfig.user_id == current_user.id)
            .order_by(DistributionSavedConfig.version.asc(), DistributionSavedConfig.created_at.asc())
            .limit(1)
        )
        baseline = fallback_result.scalar_one_or_none()
    if baseline is None:
        raise HTTPException(status_code=404, detail="no saved distribution config found")
    await _lock_user_distribution_row(db, current_user)
    max_version_result = await db.execute(
        select(func.coalesce(func.max(DistributionSavedConfig.version), 0)).where(
            DistributionSavedConfig.user_id == current_user.id
        )
    )
    next_version = int(max_version_result.scalar_one() or 0) + 1
    await db.execute(
        DistributionSavedConfig.__table__.update()
        .where(DistributionSavedConfig.user_id == current_user.id)
        .values(is_active=False)
    )
    next_config = DistributionSavedConfig(
        user_id=current_user.id,
        name=f"{baseline.name} v{next_version}",
        rows=baseline.rows,
        signature=sha1(
            json.dumps(
                {
                    "baseline": str(baseline.id),
                    "effective_from_period_start": payload.effective_from_period_start.isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        percent_mode=baseline.percent_mode,
        auto_enabled=baseline.auto_enabled,
        scope_hash=baseline.scope_hash,
        source="post_onboarding_adjustment",
        version=next_version,
        effective_from_period_start=payload.effective_from_period_start,
        is_active=True,
    )
    db.add(next_config)
    rows: list[DistributionSavedRowIn] = []
    for item in baseline.rows if isinstance(baseline.rows, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(DistributionSavedRowIn.model_validate(item))
        except Exception:
            continue
    await _apply_saved_rows_to_distribution_rules(db, current_user, rows=rows)
    await db.commit()
    await db.refresh(next_config)
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    return _to_saved_config_out(
        next_config,
        envelope_name_by_id=envelope_name_by_id,
        goal_name_by_id=goal_name_by_id,
    )


@router.post("/onboarding-status", response_model=DistributionOnboardingStatusOut)
async def get_distribution_onboarding_status(
    payload: DistributionOnboardingStatusIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionOnboardingStatusOut:
    envelope_name_by_id, goal_name_by_id = await _get_target_name_maps(db, current_user)
    envelope_ids_by_key: Dict[str, Set[UUID]] = defaultdict(set)
    envelope_key_by_id: Dict[UUID, str] = {}
    for env_id, name in envelope_name_by_id.items():
        key = distribution_name_equivalent_key(name)
        if not key:
            continue
        envelope_ids_by_key[key].add(env_id)
        envelope_key_by_id[env_id] = key
    eligible_names_payload = [
        name.strip() for name in payload.eligible_envelope_names if name.strip()
    ]
    canonical_eligible_names: List[str] = []
    latest_record_result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == current_user.id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(1)
    )
    latest_record = latest_record_result.scalar_one_or_none()
    if latest_record is not None and isinstance(latest_record.payload, dict):
        normalized_payload, _ = normalize_record_payload_for_response(
            latest_record.payload,
            stored_workflow_stage=latest_record.stage,
        )
        answers = (
            normalized_payload.get("answers")
            if isinstance(normalized_payload.get("answers"), dict)
            else {}
        )
        if answers:
            canonical_state = compute_canonical_apply_state_backend(answers)
            canonical_eligible_names = [
                name.strip()
                for name in canonical_state.distribution_eligible_names
                if isinstance(name, str) and name.strip()
            ]
    eligible_names = canonical_eligible_names or eligible_names_payload
    unresolved_envelope_names = [
        name
        for name in eligible_names
        if distribution_name_equivalent_key(name) not in envelope_ids_by_key
    ]

    eligible_keys_from_names: Set[str] = {
        distribution_name_equivalent_key(name)
        for name in eligible_names
        if distribution_name_equivalent_key(name) in envelope_ids_by_key
    }
    valid_envelope_ids = set(envelope_name_by_id.keys())
    eligible_keys_from_payload: Set[str] = {
        envelope_key_by_id[envelope_id]
        for envelope_id in payload.eligible_envelope_ids
        if envelope_id in valid_envelope_ids and envelope_id in envelope_key_by_id
    }
    # When canonical eligible names are available from the latest onboarding
    # record, they are the source of truth and must not be overridden by
    # client-provided ids (which can be stale).
    eligible_keys: Set[str]
    if canonical_eligible_names:
        eligible_keys = eligible_keys_from_names
    else:
        eligible_keys = eligible_keys_from_payload or eligible_keys_from_names
    eligible_total = len(eligible_keys)
    unresolved_total = len(unresolved_envelope_names)

    active_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
    )
    active_config = active_result.scalar_one_or_none()

    covered_ids: Set[UUID] = set()
    source: str = "none"
    if active_config is not None and isinstance(active_config.rows, list):
        source = "active_config"
        for item in active_config.rows:
            if not isinstance(item, dict):
                continue
            target_type = item.get("target_type")
            mode = item.get("mode")
            enabled = bool(item.get("enabled", True))
            target_id_raw = item.get("target_id")
            if target_type != "envelope" or not enabled or mode not in {"fixed", "percent"}:
                continue
            try:
                covered_ids.add(UUID(str(target_id_raw)))
            except Exception:
                continue
    else:
        rules_result = await db.execute(
            select(DistributionRule).where(DistributionRule.user_id == current_user.id)
        )
        rules = list(rules_result.scalars().all())
        for rule in rules:
            if (
                rule.target_type == "envelope"
                and rule.enabled
                and rule.mode in {"fixed_per_period", "percent_of_income"}
            ):
                covered_ids.add(rule.target_id)
        if covered_ids:
            source = "legacy_rules"

    covered_keys = {
        envelope_key_by_id[env_id]
        for env_id in covered_ids
        if env_id in envelope_key_by_id
    }
    covered_total = len(eligible_keys.intersection(covered_keys))
    missing_keys = [key for key in eligible_keys if key not in covered_keys]
    missing_key_set = set(missing_keys)
    missing_envelope_names: List[str] = []
    seen_missing_keys: Set[str] = set()
    for name in eligible_names:
        key = distribution_name_equivalent_key(name)
        if not key or key not in missing_key_set or key in seen_missing_keys:
            continue
        missing_envelope_names.append(name)
        seen_missing_keys.add(key)

    scope_mismatch = (
        not canonical_eligible_names
        and
        active_config is not None
        and payload.scope_hash
        and active_config.scope_hash
        and active_config.scope_hash != payload.scope_hash
    )
    if eligible_total == 0 and unresolved_total == 0:
        setup_status = "saved_valid"
        message = "لا توجد أظرفة مرنة للتوزيع في هذه المرحلة."
    elif unresolved_total > 0:
        setup_status = "invalidated"
        message = "بعض الأظرفة المرنة مازال ما تزامنوش، عاود حفظ إعداد التوزيع."
    elif scope_mismatch:
        setup_status = "invalidated"
        message = "تغيرت الأظرفة المستهدفة، خاصك تراجع إعداد التوزيع."
    elif covered_total == eligible_total:
        setup_status = "legacy_rules_detected" if source == "legacy_rules" else "saved_valid"
        message = (
            "القواعد الحالية كتغطي كامل الأظرفة المرنة."
            if source == "legacy_rules"
            else "إعداد التوزيع محفوظ وجاهز."
        )
    elif source == "none":
        setup_status = "not_started"
        message = "مازال ما تسجل حتى إعداد توزيع صالح."
    else:
        setup_status = "invalidated"
        message = "الإعداد الحالي ناقص، خاصك تكمل تغطية الأظرفة المرنة."

    active_out = (
        _to_saved_config_out(
            active_config,
            envelope_name_by_id=envelope_name_by_id,
            goal_name_by_id=goal_name_by_id,
        )
        if active_config is not None
        else None
    )
    return DistributionOnboardingStatusOut(
        setup_status=setup_status,
        eligible_total=eligible_total,
        eligible_envelope_names=eligible_names,
        covered_total=covered_total,
        unresolved_total=unresolved_total,
        unresolved_envelope_names=unresolved_envelope_names,
        missing_envelope_names=missing_envelope_names,
        source=source,
        active_config=active_out,
        message=message,
    )


@router.get("/config", response_model=DistributionConfigOut)
async def get_distribution_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionConfigOut:
    items_result = await db.execute(
        select(DistributionItem).where(DistributionItem.user_id == current_user.id)
    )
    items = list(items_result.scalars().all())
    item_map: Dict[tuple[str, UUID], DistributionItem] = {
        (item.target_type, item.target_id): item for item in items
    }

    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(False),
            Envelope.is_goal.is_(False),
        )
    )
    envelopes = list(envelopes_result.scalars().all())

    goals_result = await db.execute(
        select(Goal).where(Goal.user_id == current_user.id)
    )
    goals = list(goals_result.scalars().all())

    def build_item(
        target_type: str, target_id: UUID, name: str
    ) -> DistributionConfigItemOut:
        item = item_map.get((target_type, target_id))
        if item is None:
            return DistributionConfigItemOut(
                target_id=target_id,
                name=name,
                mode="none",
                fixed_amount=None,
                fixed_priority=None,
                percent=None,
                enabled=False,
            )
        return DistributionConfigItemOut(
            target_id=target_id,
            name=name,
            mode=item.mode,
            fixed_amount=item.fixed_amount,
            fixed_priority=item.fixed_priority,
            percent=item.percent,
            enabled=item.enabled,
        )

    return DistributionConfigOut(
        auto_enabled=current_user.auto_distribution_enabled,
        envelopes=[build_item("envelope", env.id, env.name) for env in envelopes],
        goals=[build_item("goal", goal.id, goal.name) for goal in goals],
    )


@router.put("/config", response_model=DistributionConfigOut)
async def update_distribution_config(
    payload: DistributionConfigIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionConfigOut:
    percent_total = Decimal("0.00")
    items: list[tuple[str, DistributionConfigItemOut]] = []

    async with db.begin_nested():
        envelopes_result = await db.execute(
            select(Envelope).where(
                Envelope.user_id == current_user.id,
                Envelope.is_cash.is_(False),
                Envelope.is_goal.is_(False),
            )
        )
        envelopes = {env.id: env.name for env in envelopes_result.scalars().all()}

        goals_result = await db.execute(
            select(Goal).where(Goal.user_id == current_user.id)
        )
        goals = {goal.id: goal.name for goal in goals_result.scalars().all()}

        def validate_item(target_type: str, item: DistributionConfigItemOut) -> None:
            nonlocal percent_total
            if item.mode == "fixed":
                if item.fixed_amount is None or item.fixed_priority is None:
                    raise HTTPException(status_code=400, detail="fixed requires amount and priority")
                if item.percent is not None:
                    raise HTTPException(status_code=400, detail="fixed cannot include percent")
            elif item.mode == "percent":
                if item.percent is None:
                    raise HTTPException(status_code=400, detail="percent requires value")
                if item.fixed_amount is not None or item.fixed_priority is not None:
                    raise HTTPException(status_code=400, detail="percent cannot include fixed fields")
                if item.enabled:
                    percent_total += Decimal(str(item.percent))
            elif item.mode == "none":
                pass
            else:
                raise HTTPException(status_code=400, detail="invalid mode")

        for item in payload.envelopes:
            if item.target_id not in envelopes:
                raise HTTPException(status_code=400, detail="invalid envelope target")
            out = DistributionConfigItemOut(name=envelopes[item.target_id], **item.model_dump())
            validate_item("envelope", out)
            items.append(("envelope", out))

        for item in payload.goals:
            if item.target_id not in goals:
                raise HTTPException(status_code=400, detail="invalid goal target")
            out = DistributionConfigItemOut(name=goals[item.target_id], **item.model_dump())
            validate_item("goal", out)
            items.append(("goal", out))

        if percent_total > Decimal("100.00"):
            raise HTTPException(status_code=400, detail="percent total > 100")

        current_user.auto_distribution_enabled = payload.auto_enabled
        await db.execute(
            DistributionItem.__table__.delete().where(
                DistributionItem.user_id == current_user.id
            )
        )
        for target_type, item in items:
            if item.mode == "none":
                continue
            db.add(
                DistributionItem(
                    user_id=current_user.id,
                    target_type=target_type,
                    target_id=item.target_id,
                    mode=item.mode,
                    fixed_amount=item.fixed_amount,
                    fixed_priority=item.fixed_priority,
                    percent=item.percent,
                    enabled=item.enabled,
                )
            )

    await db.commit()
    await db.refresh(current_user)
    return await get_distribution_config(db=db, current_user=current_user)


@router.post("/simulate", response_model=DistributionSimulateOut)
async def simulate_distribution(
    payload: DistributionSimulateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionSimulateOut:
    as_of, period_start, period_end = await _current_period_bounds(
        db, current_user, payload.occurred_on
    )

    rules = await get_effective_distribution_rules(db, current_user)

    cash_available = await _cash_balance_for_period(
        db, current_user, period_start, period_end
    )
    if payload.income_amount is None and not payload.use_cash_available:
        raise HTTPException(status_code=400, detail="income_amount or use_cash_available required")

    if payload.use_cash_available:
        cash_before = cash_available
        base_amount = cash_available
        apply_income_filter = False
    else:
        cash_before = Decimal(str(payload.income_amount or 0))
        base_amount = cash_before
        apply_income_filter = True

    plan = await build_distribution_plan(
        db=db,
        user=current_user,
        ctx=DistributionContext(
            occurred_on=as_of, period_start=period_start, period_end=period_end
        ),
        rules=rules,
        cash_available=cash_before,
        base_amount=base_amount,
        apply_income_filter=apply_income_filter,
    )

    rule_lookup = {rule.id: rule for rule in rules}
    plan_amount_by_rule: dict[UUID, Decimal] = {}
    plan_name_by_rule: dict[UUID, str] = {}
    for item in plan:
        plan_amount_by_rule[item.rule_id] = item.amount
        plan_name_by_rule[item.rule_id] = item.target_name

    total = sum((item.amount for item in plan), Decimal("0.00"))
    fixed_total = sum(
        (
            item.amount
            for item in plan
            if rule_lookup.get(item.rule_id)
            and rule_lookup[item.rule_id].mode == "fixed_per_period"
        ),
        Decimal("0.00"),
    )
    remaining_after_fixed = cash_before - fixed_total
    remaining_after_percent = cash_before - total
    warnings: list[str] = []

    fixed_requested = sum(
        (
            Decimal(str(rule.amount or 0))
            for rule in rules
            if rule.enabled and rule.mode == "fixed_per_period"
            and (not apply_income_filter or rule.auto_apply_on_income)
        ),
        Decimal("0.00"),
    )
    if cash_before < fixed_requested and fixed_requested > 0:
        warnings.append("Cash insuffisant: fixes partiellement appliqués.")

    percent_total = sum(
        (
            Decimal(str(rule.percent or 0))
            for rule in rules
            if rule.enabled and rule.mode == "percent_of_income"
            and (not apply_income_filter or rule.auto_apply_on_income)
        ),
        Decimal("0.00"),
    )
    if percent_total > Decimal("100.00"):
        warnings.append("Total % > 100, normalisé automatiquement.")

    eligible_rules = [
        rule
        for rule in rules
        if rule.enabled
        and rule.mode in {"fixed_per_period", "percent_of_income"}
        and (not apply_income_filter or rule.auto_apply_on_income)
    ]

    eligible_envelope_ids = {
        rule.target_id
        for rule in eligible_rules
        if rule.target_type == "envelope"
    }
    eligible_goal_ids = {
        rule.target_id
        for rule in eligible_rules
        if rule.target_type == "goal"
    }
    envelope_name_by_id: dict[UUID, str] = {}
    goal_name_by_id: dict[UUID, str] = {}
    if eligible_envelope_ids:
        envelope_result = await db.execute(
            select(Envelope).where(
                Envelope.user_id == current_user.id,
                Envelope.id.in_(eligible_envelope_ids),
            )
        )
        envelope_name_by_id = {item.id: item.name for item in envelope_result.scalars().all()}
    if eligible_goal_ids:
        goal_result = await db.execute(
            select(Goal).where(
                Goal.user_id == current_user.id,
                Goal.id.in_(eligible_goal_ids),
            )
        )
        goal_name_by_id = {item.id: item.name for item in goal_result.scalars().all()}

    items_out = []
    for rule in eligible_rules:
        rule_name = plan_name_by_rule.get(rule.id)
        if not rule_name:
            rule_name = (
                envelope_name_by_id.get(rule.target_id)
                if rule.target_type == "envelope"
                else goal_name_by_id.get(rule.target_id)
            ) or str(rule.target_id)
        items_out.append(
            {
                "target_type": rule.target_type,
                "target_id": rule.target_id,
                "name": rule_name,
                "mode": "fixed" if rule.mode == "fixed_per_period" else "percent",
                "amount": plan_amount_by_rule.get(rule.id, Decimal("0.00")),
                "fixed_priority": rule.priority if rule.mode == "fixed_per_period" else None,
            }
        )

    return DistributionSimulateOut(
        period_start=period_start,
        period_end=period_end,
        cash_before=cash_before,
        cash_after=cash_before - total,
        remaining_after_fixed=remaining_after_fixed,
        remaining_after_percent=remaining_after_percent,
        items=items_out,
        warnings=warnings,
    )


@router.post("/apply", response_model=DistributionApplyOut)
async def apply_distribution(
    payload: DistributionApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionApplyOut:
    as_of, period_start, period_end = await _current_period_bounds(
        db, current_user, payload.occurred_on
    )

    rules = await get_effective_distribution_rules(db, current_user)

    cash_available = await _cash_balance_for_period(
        db, current_user, period_start, period_end
    )
    if payload.income_amount is None and not payload.use_cash_available:
        raise HTTPException(status_code=400, detail="income_amount or use_cash_available required")

    base_amount = (
        Decimal(str(payload.income_amount))
        if payload.income_amount is not None
        else cash_available
    )
    apply_income_filter = payload.income_amount is not None and not payload.use_cash_available

    plan = await build_distribution_plan(
        db=db,
        user=current_user,
        ctx=DistributionContext(
            occurred_on=as_of, period_start=period_start, period_end=period_end
        ),
        rules=rules,
        cash_available=cash_available,
        base_amount=base_amount,
        apply_income_filter=apply_income_filter,
    )
    active_cfg = await _active_saved_config_for_date(db, current_user, period_start)

    log = await apply_distribution_plan(
        db=db,
        user=current_user,
        ctx=DistributionContext(
            occurred_on=as_of, period_start=period_start, period_end=period_end
        ),
        plan=plan,
        trigger="manual_apply",
        transaction_id=None,
        income_amount=payload.income_amount,
        config_id=active_cfg.id if active_cfg is not None else None,
        config_version=active_cfg.version if active_cfg is not None else None,
    )

    await db.commit()
    return DistributionApplyOut(
        run_id=log.id,
        cash_before=cash_available,
        cash_after=log.cash_after,
        total_distributed=Decimal(str(log.cash_before)) - Decimal(str(log.cash_after)),
        warnings=[],
    )
