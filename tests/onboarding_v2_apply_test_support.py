from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, CategoryEnvelopeMap, DistributionRule, Envelope, Goal, User
from app.services.onboarding_v2_payload_normalization import normalize_onboarding_answers


def build_answers(
    *, include_explicit_envelope_answers: bool, modernize: bool = False
) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "Q0_income_type": "salaried",
        "S2a_salary_amount": "6000",
        "S3_frequency": "monthly",
        "SWP1_last_income_date": "2026-04-01",
        "E3_housing_status": "rent",
        "RNT0_rent_amount": "2000",
        "E4_transport_mode": "public",
        "TRP1_public_monthly_amount": "200",
        "FX1_fixed_items": ["bills", "internet_phone"],
        "FX2_amount_bills": "300",
        "FX2_amount_internet_phone": "100",
        "E5_has_debt": "yes",
        "D1_debt_count": "1",
        "D2_debt_name_1": "Visa",
        "D3_debt_remaining_amount_1": "2400",
        "D4_debt_native_amount_1": "200",
        "D4_debt_payment_cadence_1": "monthly",
        "G0_has_goal": "yes",
        "G1_goal_count": "1",
        "G1_goal_name_1": "Voyage",
        "G1_goal_type_1": "travel",
        "G1_goal_target_amount_1": "1200",
        "G1_goal_importance_1": "important",
        "P1_debt_priority": "debt_relief_fast",
        "P1_goal_priority": "goal_start_light",
        "P1_living_priority": "living_balance",
        "F1_guidance_planned_goals": "150",
        "E10_keep_suggestions": [
            "loyer",
            "factures",
            "transport",
            "dettes_visa",
            "objectif_voyage",
        ],
    }
    if include_explicit_envelope_answers:
        answers["E11_selected_envelopes_v1"] = [
            {
                "name": "Loyer",
                "final_name": "Loyer",
                "group_key": "housing",
                "final_rollover_enabled": False,
                "custom_category": None,
                "custom_amount": None,
            },
            {
                "name": "Factures",
                "final_name": "Factures",
                "group_key": "bills",
                "final_rollover_enabled": False,
                "custom_category": None,
                "custom_amount": None,
            },
            {
                "name": "Transport",
                "final_name": "Transport",
                "group_key": "transport",
                "final_rollover_enabled": False,
                "custom_category": None,
                "custom_amount": None,
            },
            {
                "name": "Dettes — Visa",
                "final_name": "Dettes — Visa",
                "group_key": "debts",
                "final_rollover_enabled": False,
                "custom_category": None,
                "custom_amount": None,
            },
            {
                "name": "Objectif — Voyage",
                "final_name": "Objectif — Voyage",
                "group_key": "goals",
                "final_rollover_enabled": True,
                "custom_category": None,
                "custom_amount": None,
            },
        ]
    return normalize_onboarding_answers(answers) if modernize else answers


def draft_objects_garbage() -> dict[str, Any]:
    return {
        "goals": [
            {
                "name": "Wrong goal",
                "target_amount": 99999,
                "contribution_amount": 999,
            }
        ],
        "sanity_metrics": {"remaining": -9999},
        "reserve_plan_v1": {"starter_seed_per_cycle": 999},
        "debt_plan_v2": {"suggested_extra_per_cycle": 999, "focus_debt_name": "Wrong debt"},
        "cycle_normalized_expenses_v1": [
            {
                "envelope": "Broken",
                "per_cycle_amount": 999,
                "priority_layer": "protected",
            }
        ],
        "envelopes_proposal_v1": {
            "selected_envelopes": [
                {
                    "name": "Broken",
                    "final_name": "Broken",
                    "group_key": "essentials",
                    "final_rollover_enabled": True,
                }
            ]
        },
    }


async def serialize_user_state(db: AsyncSession, user: User) -> dict[str, Any]:
    envelopes_result = await db.execute(
        select(Envelope).where(Envelope.user_id == user.id).order_by(Envelope.name.asc())
    )
    envelopes = list(envelopes_result.scalars().all())
    categories_result = await db.execute(
        select(Category).where(Category.user_id == user.id).order_by(Category.name.asc())
    )
    categories = list(categories_result.scalars().all())
    mappings_result = await db.execute(
        select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user.id)
    )
    mappings = list(mappings_result.scalars().all())
    goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id).order_by(Goal.name.asc()))
    goals = list(goals_result.scalars().all())
    rules_result = await db.execute(
        select(DistributionRule)
        .where(DistributionRule.user_id == user.id)
        .order_by(DistributionRule.rank.asc(), DistributionRule.priority.asc())
    )
    rules = list(rules_result.scalars().all())

    category_name_by_id = {category.id: category.name for category in categories}
    envelope_name_by_id = {envelope.id: envelope.name for envelope in envelopes}
    goal_name_by_id = {goal.id: goal.name for goal in goals}

    return {
        "user": {
            "sweep_interval_days": user.sweep_interval_days,
            "next_sweep_date": user.next_sweep_date.isoformat() if user.next_sweep_date else None,
            "auto_distribution_enabled": user.auto_distribution_enabled,
        },
        "envelopes": [
            (
                envelope.name,
                envelope.rollover_enabled,
                envelope.is_goal,
                envelope.is_default_savings,
                envelope.is_cash,
            )
            for envelope in envelopes
        ],
        "categories": [category.name for category in categories],
        "mappings": sorted(
            (
                category_name_by_id.get(mapping.category_id, str(mapping.category_id)),
                envelope_name_by_id.get(mapping.envelope_id, str(mapping.envelope_id)),
            )
            for mapping in mappings
        ),
        "goals": [
            (
                goal.name,
                goal.goal_type,
                envelope_name_by_id.get(goal.envelope_id, str(goal.envelope_id)),
                str(Decimal(goal.target_amount).quantize(Decimal("0.01"))),
                str(Decimal(goal.contribution_amount).quantize(Decimal("0.01"))),
                goal.auto_contribute,
                goal.priority,
            )
            for goal in goals
        ],
        "rules": [
            (
                rule.target_type,
                envelope_name_by_id.get(rule.target_id, goal_name_by_id.get(rule.target_id, str(rule.target_id))),
                str(Decimal(rule.amount).quantize(Decimal("0.01"))) if rule.amount is not None else None,
                rule.priority,
                rule.rank,
                rule.enabled,
            )
            for rule in rules
        ],
    }
