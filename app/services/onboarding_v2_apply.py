from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    CategoryEnvelopeMap,
    DistributionRule,
    DistributionSavedConfig,
    Envelope,
    Goal,
    User,
)
from app.services.onboarding_distribution_validation import (
    build_apply_precondition_error_detail,
    validate_apply_preconditions,
)
from app.services.envelope_virtual import is_virtual_parent_envelope_name
from app.services.onboarding_v2_canonical import (
    ExistingApplyState,
    compute_canonical_apply_state_backend,
)
from app.services.onboarding_v2_record_state import (
    build_onboarding_materialized_state as _build_onboarding_materialized_state,
)
from app.services.sweep_context import infer_sweep_interval_days_from_answers
from app.services.category_catalog import EXPENSE_CATEGORY_KEYS_SQL, category_key_from_name
from app.services.category_system_seed import (
    build_system_category_mapping_plan,
    first_eligible_category_for_envelope,
)
from app.services.category_eligibility import (
    eligible_expense_category_keys_from_answers,
    prune_ineligible_categories_for_user,
)


def _safe_string(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed.quantize(Decimal("0.01"))


def _safe_date(value: Any) -> date | None:
    normalized = _safe_string(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _safe_priority(value: Any, default: int = 2) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), 3)


def _name_key(value: str) -> str:
    return value.strip().casefold()


def build_onboarding_materialized_state(summary: dict[str, Any]) -> dict[str, Any]:
    return _build_onboarding_materialized_state(
        summary,
        applied=True,
        workflow_stage="completed",
    )


def _claim_existing_envelope(
    *,
    envelope_by_name: dict[str, Envelope],
    original_name: str | None,
    final_name: str,
    allow_goal: bool = False,
) -> Envelope | None:
    if not original_name:
        return None
    if _name_key(original_name) == _name_key(final_name):
        return envelope_by_name.get(_name_key(final_name))
    if envelope_by_name.get(_name_key(final_name)) is not None:
        return envelope_by_name.get(_name_key(final_name))

    existing = envelope_by_name.get(_name_key(original_name))
    if existing is None:
        return None
    if existing.is_cash or existing.is_default_savings:
        return None
    if not allow_goal and existing.is_goal:
        return None

    old_key = _name_key(existing.name)
    existing.name = final_name
    envelope_by_name.pop(old_key, None)
    envelope_by_name[_name_key(final_name)] = existing
    return existing


async def _sync_distribution_rules_from_onboarding(
    db: AsyncSession,
    user: User,
    *,
    canonical_state,
    envelope_by_name: dict[str, Envelope],
    goal_by_name: dict[str, Goal],
    summary: dict[str, Any],
) -> None:
    active_config_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
    )
    active_config = active_config_result.scalar_one_or_none()
    active_rows: list[dict[str, Any]] = []
    if active_config is not None and isinstance(active_config.rows, list):
        active_rows = [row for row in active_config.rows if isinstance(row, dict)]

    existing_rules_result = await db.execute(
        select(DistributionRule.id).where(DistributionRule.user_id == user.id).limit(1)
    )
    existing_rule_id = existing_rules_result.scalar_one_or_none()
    if existing_rule_id is not None and active_config is None:
        summary["distribution_rules_created"] = 0
        summary["goal_distribution_rules_created"] = 0
        summary["distribution_auto_enabled"] = bool(user.auto_distribution_enabled)
        return

    layer_priority = {
        "protected": 10,
        "planned_future_obligation": 40,
        "scheduled": 70,
    }
    aggregated: dict[str, dict[str, Any]] = {}
    for item in canonical_state.cycle_normalized_expenses_v1:
        envelope_name = _safe_string(item.get("envelope"))
        per_cycle_amount = _safe_decimal(item.get("per_cycle_amount"))
        priority_layer = _safe_string(item.get("priority_layer")) or "scheduled"
        if not envelope_name or per_cycle_amount is None:
            continue
        bucket = aggregated.setdefault(
            envelope_name,
            {
                "amount": Decimal("0.00"),
                "priority": layer_priority.get(priority_layer, 70),
            },
        )
        bucket["amount"] += per_cycle_amount
        bucket["priority"] = min(int(bucket["priority"]), layer_priority.get(priority_layer, 70))

    reserve_amount = _safe_decimal(canonical_state.reserve_plan_v1.get("starter_seed_per_cycle"))
    if reserve_amount is not None and reserve_amount > 0:
        reserve_target_name = _safe_string(canonical_state.reserve_plan_v1.get("target_envelope_name"))
        reserve_name = reserve_target_name if reserve_target_name and _name_key(reserve_target_name) in envelope_by_name else (
            "Epargnes" if "epargnes" in envelope_by_name else "Épargne"
        )
        reserve_bucket = aggregated.setdefault(
            reserve_name,
            {"amount": Decimal("0.00"), "priority": 85},
        )
        reserve_bucket["amount"] += reserve_amount
        reserve_bucket["priority"] = min(int(reserve_bucket["priority"]), 85)

    suggested_extra = _safe_decimal(canonical_state.debt_plan_v2.get("suggested_extra_per_cycle"))
    debt_candidates = [
        (
            _safe_string(item.get("envelope_name")) or _safe_string(item.get("name")),
            _safe_string(item.get("name")),
        )
        for item in canonical_state.debts
    ]
    debt_candidates = [(final_name, original_name) for final_name, original_name in debt_candidates if final_name]
    if suggested_extra is not None and suggested_extra > 0 and debt_candidates:
        if canonical_state.debt_posture == "focus":
            focus_name = _safe_string(canonical_state.debt_plan_v2.get("focus_debt_name"))
            chosen_name = None
            if focus_name:
                for final_name, original_name in debt_candidates:
                    haystack = f"{final_name} {original_name or ''}".casefold()
                    if focus_name.casefold() in haystack or haystack in focus_name.casefold():
                        chosen_name = final_name
                        break
            chosen_name = chosen_name or debt_candidates[0][0]
            bucket = aggregated.setdefault(chosen_name, {"amount": Decimal("0.00"), "priority": 95})
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

    active_fixed_targets: set[tuple[str, UUID]] = set()
    for item in sorted(active_rows, key=lambda row: int(row.get("rank") or 1)):
        if bool(item.get("enabled", True)) is False:
            continue
        if str(item.get("mode") or "") != "fixed":
            continue
        target_type = str(item.get("target_type") or "")
        if target_type not in {"envelope", "goal"}:
            continue
        try:
            target_id = UUID(str(item.get("target_id")))
        except Exception:
            continue
        active_fixed_targets.add((target_type, target_id))

    await db.execute(delete(DistributionRule).where(DistributionRule.user_id == user.id))

    created = 0
    rank = 1
    for envelope_name, payload in sorted(
        aggregated.items(),
        key=lambda item: (int(item[1]["priority"]), item[0].casefold()),
    ):
        envelope = envelope_by_name.get(_name_key(envelope_name))
        if envelope is None:
            continue
        amount = payload["amount"].quantize(Decimal("0.01"))
        if amount <= 0:
            continue
        if ("envelope", envelope.id) in active_fixed_targets:
            continue
        db.add(
            DistributionRule(
                user_id=user.id,
                target_type="envelope",
                target_id=envelope.id,
                mode="fixed_per_period",
                amount=amount,
                percent=None,
                priority=int(payload["priority"]),
                rank=rank,
                enabled=True,
                auto_apply_on_income=True,
            )
        )
        created += 1
        rank += 1

    auto_goal_rules = 0
    for source in [*canonical_state.goals, *canonical_state.sinking_funds]:
        name = _safe_string(source.get("name"))
        contribution_amount = _safe_decimal(source.get("contribution_amount"))
        auto_contribute = bool(source.get("auto_contribute"))
        if not name or contribution_amount is None or contribution_amount <= 0 or not auto_contribute:
            continue
        goal = goal_by_name.get(_name_key(name))
        if goal is None:
            continue
        if ("goal", goal.id) in active_fixed_targets:
            continue
        db.add(
            DistributionRule(
                user_id=user.id,
                target_type="goal",
                target_id=goal.id,
                mode="fixed_per_period",
                amount=contribution_amount.quantize(Decimal("0.01")),
                percent=None,
                priority=120 if goal.goal_type == "goal" else 60,
                rank=rank,
                enabled=True,
                auto_apply_on_income=True,
            )
        )
        created += 1
        auto_goal_rules += 1
        rank += 1

    for item in sorted(active_rows, key=lambda row: int(row.get("rank") or 1)):
        target_type = str(item.get("target_type") or "")
        if target_type not in {"envelope", "goal"}:
            continue
        mode = str(item.get("mode") or "")
        if mode not in {"fixed", "percent"}:
            continue
        if bool(item.get("enabled", True)) is False:
            continue
        target_id_raw = item.get("target_id")
        try:
            target_id = UUID(str(target_id_raw))
        except Exception:
            continue

        amount = _safe_decimal(item.get("fixed_amount")) if mode == "fixed" else None
        percent = _safe_decimal(item.get("percent")) if mode == "percent" else None
        if mode == "fixed" and (amount is None or amount <= 0):
            continue
        if mode == "percent" and (percent is None or percent <= 0):
            continue

        db.add(
            DistributionRule(
                user_id=user.id,
                target_type=target_type,
                target_id=target_id,
                mode="fixed_per_period" if mode == "fixed" else "percent_of_income",
                amount=amount if mode == "fixed" else None,
                percent=percent if mode == "percent" else None,
                priority=100,
                rank=rank,
                enabled=True,
                auto_apply_on_income=True,
            )
        )
        created += 1
        rank += 1

    summary["distribution_rules_created"] = created
    summary["goal_distribution_rules_created"] = auto_goal_rules
    if active_config is not None:
        summary["distribution_auto_enabled"] = bool(active_config.auto_enabled)
        user.auto_distribution_enabled = bool(active_config.auto_enabled)
    else:
        summary["distribution_auto_enabled"] = bool(created)
        if created:
            user.auto_distribution_enabled = True


async def apply_onboarding_v2_payload(
    db: AsyncSession,
    user: User,
    *,
    answers: dict[str, Any] | None,
    draft_objects: dict[str, Any] | None,
) -> dict[str, Any]:
    answers = answers if isinstance(answers, dict) else {}
    del draft_objects

    envelopes_result = await db.execute(select(Envelope).where(Envelope.user_id == user.id))
    envelopes = list(envelopes_result.scalars().all())
    envelope_by_name = {_name_key(env.name): env for env in envelopes}

    categories_result = await db.execute(select(Category).where(Category.user_id == user.id))
    categories = list(categories_result.scalars().all())
    category_by_name = {_name_key(category.name): category for category in categories}

    mappings_result = await db.execute(
        select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user.id)
    )
    mappings = list(mappings_result.scalars().all())
    mapping_by_category_id = {mapping.category_id: mapping for mapping in mappings}

    goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id))
    goals = list(goals_result.scalars().all())
    goal_by_name = {_name_key(goal.name): goal for goal in goals}

    canonical_state = compute_canonical_apply_state_backend(
        answers,
        existing_state=ExistingApplyState(
            envelope_names={env.name for env in envelopes},
            goal_names={goal.name for goal in goals},
        ),
    )
    eligible_category_keys = eligible_expense_category_keys_from_answers(answers)
    validation_result = await validate_apply_preconditions(
        db,
        current_user=user,
        canonical_state=canonical_state,
    )
    if not validation_result.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=build_apply_precondition_error_detail(validation_result),
        )

    summary = {
        "workflow_stage": "completed",
        "validation_stage": "valid",
        "materialization_stage": "applied",
        "state_is_consistent": True,
        "state_inconsistency_code": None,
        "state_inconsistency_message": None,
        "selected_envelopes_count": 0,
        "selected_rollover_on_count": 0,
        "envelopes_created": 0,
        "envelopes_updated": 0,
        "categories_created": 0,
        "mappings_upserted": 0,
        "mappings_skipped_unresolved": 0,
        "goals_created": 0,
        "goals_updated": 0,
        "sinking_funds_created": 0,
        "sinking_funds_updated": 0,
        "sweep_interval_days": user.sweep_interval_days,
        "distribution_posture_v1": canonical_state.distribution_posture_v1,
        "financial_priority_profile": canonical_state.financial_priority_profile,
        "debt_posture": canonical_state.debt_posture,
        "goal_posture": canonical_state.goal_posture,
        "living_margin_level": canonical_state.living_margin_level,
        "reserve_policy": canonical_state.reserve_policy,
        "reserve_level": canonical_state.reserve_level,
        "confidence_label": canonical_state.confidence_label,
        "sinking_fund_policy": canonical_state.sinking_fund_policy,
        "cash_flow_timing_v1": canonical_state.cash_flow_timing_v1,
        "reserve_plan_v1": canonical_state.reserve_plan_v1,
        "priority_explanation_lines": canonical_state.priority_explanation_lines,
        "distribution_setup_valid": validation_result.is_valid,
        "distribution_setup_source": validation_result.distribution_source,
        "distribution_status": validation_result.distribution_status,
        "distribution_source": validation_result.distribution_source,
        "validation_warnings": validation_result.warnings,
        "distribution_validation_warnings": validation_result.warnings,
        "distribution_eligible_total": validation_result.eligible_total,
        "distribution_covered_total": validation_result.covered_total,
        "distribution_unresolved_total": validation_result.unresolved_total,
        "distribution_unresolved_envelope_names": validation_result.unresolved_envelope_names,
        "distribution_missing_envelope_names": validation_result.missing_envelope_names,
        "distribution_active_config_id": validation_result.active_config_id,
    }

    remaining_raw = canonical_state.sanity_metrics.get("remaining")
    try:
        remaining_value = float(remaining_raw)
    except (TypeError, ValueError):
        remaining_value = 0.0
    summary["cashflow_remaining_monthly"] = max(0.0, remaining_value)
    summary["cashflow_overcommit_monthly"] = max(0.0, -remaining_value)

    sweep_interval_days = infer_sweep_interval_days_from_answers(answers)
    if sweep_interval_days > 0 and user.sweep_interval_days != sweep_interval_days:
        user.sweep_interval_days = sweep_interval_days
    summary["sweep_interval_days"] = user.sweep_interval_days

    bootstrap_anchor_date = _safe_date(
        canonical_state.cash_flow_timing_v1.get("last_income_date") or answers.get("SWP1_last_income_date")
    )
    if isinstance(bootstrap_anchor_date, date):
        user.next_sweep_date = bootstrap_anchor_date + timedelta(days=user.sweep_interval_days)

    explicit_custom_category_pairs: list[tuple[str, str]] = []
    selected_envelopes_materialized: list[dict[str, Any]] = []
    selected_envelope_name_keys: set[str] = set()
    envelope_name_aliases: dict[str, str] = {
        "epargne": "Epargnes",
        "épargne": "Epargnes",
        "savings": "Epargnes",
        "saving": "Epargnes",
        "cash": "Cash",
    }

    for item in canonical_state.selected_envelopes:
        original_name = _safe_string(item.get("name"))
        final_name = _safe_string(item.get("final_name")) or original_name
        if not final_name:
            continue
        if is_virtual_parent_envelope_name(final_name):
            # "Morona/Flexibility" is a virtual parent used for setup only.
            # It must not be materialized as a real envelope in DB.
            continue
        if original_name:
            envelope_name_aliases[_name_key(original_name)] = final_name
        if _name_key(final_name) in {"cash", "epargnes"}:
            continue
        if _safe_string(item.get("group_key")) == "goals":
            continue

        selected_envelope_name_keys.add(_name_key(final_name))
        summary["selected_envelopes_count"] += 1
        final_rollover = bool(item.get("final_rollover_enabled"))
        if final_rollover:
            summary["selected_rollover_on_count"] += 1

        existing = envelope_by_name.get(_name_key(final_name))
        renamed_existing = False
        if existing is None:
            claimed = _claim_existing_envelope(
                envelope_by_name=envelope_by_name,
                original_name=original_name,
                final_name=final_name,
            )
            if claimed is not None:
                existing = claimed
                renamed_existing = True
        if existing is None:
            existing = Envelope(
                user_id=user.id,
                name=final_name,
                rollover_enabled=final_rollover,
                is_default_savings=False,
                is_cash=False,
                is_goal=False,
                deletable=True,
            )
            db.add(existing)
            await db.flush()
            envelope_by_name[_name_key(final_name)] = existing
            summary["envelopes_created"] += 1
        elif not existing.is_cash and not existing.is_default_savings and not existing.is_goal:
            was_updated = renamed_existing
            if existing.rollover_enabled != final_rollover:
                existing.rollover_enabled = final_rollover
                was_updated = True
            if was_updated:
                summary["envelopes_updated"] += 1

        custom_category = _safe_string(item.get("custom_category"))
        if custom_category:
            explicit_custom_category_pairs.append((custom_category, final_name))
        selected_envelopes_materialized.append(
            {
                "name": original_name or final_name,
                "final_name": final_name,
                "group_key": _safe_string(item.get("group_key")),
                "custom_category": custom_category,
            }
        )

    # Safety net: materialize envelopes referenced by normalized fixed expenses
    # even when they were not explicitly selected in E11_selected_envelopes_v1.
    # This covers custom fixed "other" rows and family-support envelopes.
    for expense_item in canonical_state.cycle_normalized_expenses_v1:
        envelope_name = _safe_string(expense_item.get("envelope"))
        if not envelope_name:
            continue
        if _name_key(envelope_name) in {"cash", "epargnes"}:
            continue
        if is_virtual_parent_envelope_name(envelope_name):
            continue
        if _name_key(envelope_name) in selected_envelope_name_keys:
            continue
        if envelope_by_name.get(_name_key(envelope_name)) is not None:
            continue

        # Fixed-expense-derived envelopes should keep rollover enabled.
        envelope = Envelope(
            user_id=user.id,
            name=envelope_name,
            rollover_enabled=True,
            is_default_savings=False,
            is_cash=False,
            is_goal=False,
            deletable=True,
        )
        db.add(envelope)
        await db.flush()
        envelope_by_name[_name_key(envelope_name)] = envelope
        summary["envelopes_created"] += 1

    system_mapping_plan = build_system_category_mapping_plan(
        selected_envelopes=selected_envelopes_materialized,
        include_full_group_set=True,
        has_children=any(key in eligible_category_keys for key in {"children_school", "children_activities", "childcare"}),
        has_business_activity=any(key in eligible_category_keys for key in {"business_tools", "business_travel", "freelance_expenses"}),
        has_vehicle=any(key in eligible_category_keys for key in {"transport_fuel", "transport_parking", "transport_maintenance", "car_insurance"}),
        has_debt=any(key in eligible_category_keys for key in {"debt_payment", "debt_extra_payment", "taxes"}),
    )

    planned_category_to_envelope: dict[str, str] = dict(system_mapping_plan)
    for item in canonical_state.mappings:
        category_name = _safe_string(item.get("category"))
        envelope_name = _safe_string(item.get("envelope"))
        if category_name and envelope_name:
            key = category_key_from_name(category_name)
            if key in eligible_category_keys:
                planned_category_to_envelope[key] = envelope_name
    system_expense_keys = set(EXPENSE_CATEGORY_KEYS_SQL)
    for category_name, envelope_name in explicit_custom_category_pairs:
        custom_key = category_key_from_name(category_name)
        is_system_key = custom_key in system_expense_keys
        if custom_key in eligible_category_keys or not is_system_key:
            planned_category_to_envelope[custom_key] = envelope_name

    category_names: set[str] = set(planned_category_to_envelope.keys())
    for value in canonical_state.categories:
        normalized = _safe_string(value)
        if normalized:
            key = category_key_from_name(normalized)
            if key in eligible_category_keys:
                category_names.add(key)

    for category_name in sorted(category_names, key=str.casefold):
        key = _name_key(category_name)
        if key in category_by_name:
            continue
        category = Category(user_id=user.id, name=category_name)
        db.add(category)
        await db.flush()
        category_by_name[key] = category
        summary["categories_created"] += 1

    deleted_ineligible_categories = await prune_ineligible_categories_for_user(
        db,
        user.id,
        eligible_keys=eligible_category_keys,
    )
    if deleted_ineligible_categories:
        summary["categories_pruned"] = deleted_ineligible_categories
        # prune_ineligible_categories_for_user deletes categories (and dependent mappings)
        # directly in DB. Refresh in-memory indexes so we don't upsert mappings
        # against stale/deleted category ids.
        categories_result = await db.execute(select(Category).where(Category.user_id == user.id))
        categories = list(categories_result.scalars().all())
        category_by_name = {_name_key(category.name): category for category in categories}
        mappings_result = await db.execute(
            select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user.id)
        )
        mappings = list(mappings_result.scalars().all())
        mapping_by_category_id = {mapping.category_id: mapping for mapping in mappings}

    def _resolve_envelope(envelope_name: str) -> Envelope | None:
        direct = envelope_by_name.get(_name_key(envelope_name))
        if direct is not None:
            return direct
        alias = envelope_name_aliases.get(_name_key(envelope_name))
        if not alias:
            return None
        return envelope_by_name.get(_name_key(alias))

    def _upsert_mapping(category_name: str, envelope_name: str) -> None:
        category = category_by_name.get(_name_key(category_key_from_name(category_name)))
        envelope = _resolve_envelope(envelope_name)
        if category is None or envelope is None:
            summary["mappings_skipped_unresolved"] += 1
            return
        mapping = mapping_by_category_id.get(category.id)
        if mapping is None:
            mapping = CategoryEnvelopeMap(
                user_id=user.id,
                category_id=category.id,
                envelope_id=envelope.id,
            )
            db.add(mapping)
            mapping_by_category_id[category.id] = mapping
            summary["mappings_upserted"] += 1
            return
        if mapping.envelope_id != envelope.id:
            mapping.envelope_id = envelope.id
            summary["mappings_upserted"] += 1

    for category_name, envelope_name in planned_category_to_envelope.items():
        _upsert_mapping(category_name, envelope_name)

    # Hard guarantee: every materialized non-system envelope must have at least one
    # mapped system category right after onboarding apply.
    mapped_envelope_ids = {
        mapping.envelope_id
        for mapping in mapping_by_category_id.values()
    }
    for item in selected_envelopes_materialized:
        final_name = _safe_string(item.get("final_name")) or _safe_string(item.get("name"))
        if not final_name:
            continue
        envelope = _resolve_envelope(final_name)
        if envelope is None:
            continue
        if envelope.id in mapped_envelope_ids:
            continue
        primary_key = first_eligible_category_for_envelope(
            envelope_name=final_name,
            group_key=_safe_string(item.get("group_key")),
            eligible_keys=eligible_category_keys,
        )
        if primary_key is None:
            continue
        key = _name_key(primary_key)
        category = category_by_name.get(key)
        if category is None:
            category = Category(user_id=user.id, name=primary_key)
            db.add(category)
            await db.flush()
            category_by_name[key] = category
            summary["categories_created"] += 1
        mapping = mapping_by_category_id.get(category.id)
        if mapping is None:
            mapping = CategoryEnvelopeMap(
                user_id=user.id,
                category_id=category.id,
                envelope_id=envelope.id,
            )
            db.add(mapping)
            mapping_by_category_id[category.id] = mapping
            summary["mappings_upserted"] += 1
        elif mapping.envelope_id != envelope.id:
            mapping.envelope_id = envelope.id
            summary["mappings_upserted"] += 1
        mapped_envelope_ids.add(envelope.id)

    for item in canonical_state.goals:
        goal_name = _safe_string(item.get("name"))
        target_amount = _safe_decimal(item.get("target_amount"))
        if not goal_name or target_amount is None:
            continue

        goal_target_date = _safe_date(item.get("target_date"))
        contribution_amount = _safe_decimal(item.get("contribution_amount")) or Decimal("0.00")
        auto_contribute = bool(item.get("auto_contribute"))
        priority = _safe_priority(item.get("priority"), 2)
        goal_type = _safe_string(item.get("goal_type")) or "goal"
        goal_envelope_name = _safe_string(item.get("envelope_name")) or goal_name

        envelope = envelope_by_name.get(_name_key(goal_envelope_name))
        renamed_goal_envelope = False
        if envelope is None:
            claimed = _claim_existing_envelope(
                envelope_by_name=envelope_by_name,
                original_name=goal_name,
                final_name=goal_envelope_name,
                allow_goal=True,
            )
            if claimed is not None:
                envelope = claimed
                renamed_goal_envelope = True
        if envelope is None:
            envelope = Envelope(
                user_id=user.id,
                name=goal_envelope_name,
                rollover_enabled=True,
                is_default_savings=False,
                is_cash=False,
                is_goal=True,
                deletable=True,
            )
            db.add(envelope)
            await db.flush()
            envelope_by_name[_name_key(goal_envelope_name)] = envelope
            summary["envelopes_created"] += 1
        else:
            if envelope.name != goal_envelope_name:
                envelope.name = goal_envelope_name
                renamed_goal_envelope = True
            envelope.is_goal = True
            envelope.rollover_enabled = True
            if renamed_goal_envelope:
                summary["envelopes_updated"] += 1

        existing_goal = goal_by_name.get(_name_key(goal_name))
        if existing_goal is None:
            goal = Goal(
                user_id=user.id,
                envelope_id=envelope.id,
                name=goal_name,
                goal_type=goal_type,
                target_amount=target_amount,
                target_date=goal_target_date,
                contribution_amount=contribution_amount,
                auto_contribute=auto_contribute,
                priority=priority,
            )
            db.add(goal)
            await db.flush()
            goal_by_name[_name_key(goal_name)] = goal
            summary["goals_created"] += 1
        else:
            existing_goal.envelope_id = envelope.id
            existing_goal.name = goal_name
            existing_goal.goal_type = goal_type
            existing_goal.target_amount = target_amount
            existing_goal.target_date = goal_target_date
            existing_goal.contribution_amount = contribution_amount
            existing_goal.auto_contribute = auto_contribute
            existing_goal.priority = priority
            summary["goals_updated"] += 1

    for item in canonical_state.sinking_funds:
        fund_name = _safe_string(item.get("name"))
        target_amount = _safe_decimal(item.get("target_amount"))
        if not fund_name or target_amount is None:
            continue

        contribution_amount = _safe_decimal(item.get("contribution_amount")) or Decimal("0.00")
        auto_contribute = bool(item.get("auto_contribute"))
        priority = _safe_priority(item.get("priority"), 1)
        envelope_name = _safe_string(item.get("envelope_name")) or fund_name

        envelope = envelope_by_name.get(_name_key(envelope_name))
        if envelope is None:
            envelope = Envelope(
                user_id=user.id,
                name=envelope_name,
                rollover_enabled=True,
                is_default_savings=False,
                is_cash=False,
                is_goal=True,
                deletable=True,
            )
            db.add(envelope)
            await db.flush()
            envelope_by_name[_name_key(envelope_name)] = envelope
            summary["envelopes_created"] += 1
        else:
            envelope.is_goal = True
            envelope.rollover_enabled = True

        existing_goal = goal_by_name.get(_name_key(fund_name))
        if existing_goal is None:
            goal = Goal(
                user_id=user.id,
                envelope_id=envelope.id,
                name=fund_name,
                goal_type="sinking_fund",
                target_amount=target_amount,
                target_date=None,
                contribution_amount=contribution_amount,
                auto_contribute=auto_contribute,
                priority=priority,
            )
            db.add(goal)
            await db.flush()
            goal_by_name[_name_key(fund_name)] = goal
            summary["sinking_funds_created"] += 1
        else:
            existing_goal.envelope_id = envelope.id
            existing_goal.name = fund_name
            existing_goal.goal_type = "sinking_fund"
            existing_goal.target_amount = target_amount
            existing_goal.target_date = None
            existing_goal.contribution_amount = contribution_amount
            existing_goal.auto_contribute = auto_contribute
            existing_goal.priority = priority
            summary["sinking_funds_updated"] += 1

    await _sync_distribution_rules_from_onboarding(
        db,
        user,
        canonical_state=canonical_state,
        envelope_by_name=envelope_by_name,
        goal_by_name=goal_by_name,
        summary=summary,
    )

    return summary
