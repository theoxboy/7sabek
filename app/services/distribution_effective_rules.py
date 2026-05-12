from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DistributionRule, Envelope, Goal, OnboardingV2Record, User
from app.services.distribution_name_normalization import distribution_name_equivalent_key
from app.services.onboarding_v2_canonical import compute_canonical_apply_state_backend


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


async def _build_baseline_fixed_rules(
    db: AsyncSession,
    user: User,
) -> list[DistributionRule]:
    envelopes_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == user.id,
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

    goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id))
    goals = list(goals_result.scalars().all())
    goal_by_key = {
        distribution_name_equivalent_key(goal.name): goal
        for goal in goals
        if distribution_name_equivalent_key(goal.name)
    }

    latest_record_result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == user.id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(1)
    )
    latest_record = latest_record_result.scalar_one_or_none()
    payload = latest_record.payload if latest_record and isinstance(latest_record.payload, dict) else {}
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    if not answers:
        return []

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
        reserve_bucket = aggregated.setdefault(
            reserve_target_envelope.name,
            {"amount": Decimal("0.00"), "priority": 85},
        )
        reserve_bucket["amount"] += reserve_amount
        reserve_bucket["priority"] = min(int(reserve_bucket["priority"]), 85)

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

    baseline: list[DistributionRule] = []
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
        baseline.append(
            DistributionRule(
                id=uuid4(),
                user_id=user.id,
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
        baseline.append(
            DistributionRule(
                id=uuid4(),
                user_id=user.id,
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

    return baseline


async def get_effective_distribution_rules(
    db: AsyncSession,
    user: User,
    *,
    include_disabled: bool = True,
) -> list[DistributionRule]:
    existing_rules_result = await db.execute(
        select(DistributionRule)
        .where(DistributionRule.user_id == user.id)
        .order_by(DistributionRule.rank.asc(), DistributionRule.created_at.asc())
    )
    existing_rules = list(existing_rules_result.scalars().all())
    if not include_disabled:
        existing_rules = [rule for rule in existing_rules if rule.enabled]

    baseline_rules = await _build_baseline_fixed_rules(db, user)
    if not baseline_rules:
        return existing_rules

    existing_fixed_targets = {
        (rule.target_type, rule.target_id)
        for rule in existing_rules
        if rule.enabled and rule.mode == "fixed_per_period"
    }
    next_rank = max((int(rule.rank or 1) for rule in existing_rules), default=0) + 1
    merged = list(existing_rules)
    for baseline_rule in baseline_rules:
        target_key = (baseline_rule.target_type, baseline_rule.target_id)
        if target_key in existing_fixed_targets:
            continue
        baseline_rule.rank = next_rank
        next_rank += 1
        merged.append(baseline_rule)

    return sorted(merged, key=lambda rule: int(rule.rank or 1))
