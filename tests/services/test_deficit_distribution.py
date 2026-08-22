from __future__ import annotations

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import User, Envelope, Goal, DistributionRule, OnboardingV2Record
from app.services.distribution_engine import build_distribution_plan, DistributionContext


@pytest.fixture()
async def db_session(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_deficit_distribution_waterfall(db_session: AsyncSession) -> None:
    # 1. Create User
    user = User(
        email=f"deficit-test-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()

    # 2. Add Onboarding record with expected_income_amount
    record = OnboardingV2Record(
        user_id=user.id,
        payload={
            "answers": {
                "Q0_income_type": "salaried",
                "S3_frequency": "monthly",
            },
            "draft_objects": {
                "sweep_bootstrap_v1": {
                    "last_income_date": "2026-06-01",
                    "last_income_amount": "5000",
                    "expected_income_amount": "5000",
                }
            }
        }
    )
    db_session.add(record)
    await db_session.flush()

    # 3. Create envelopes and goals
    fixed_env = Envelope(user_id=user.id, name="Loyer", rollover_enabled=False)
    flex_env = Envelope(user_id=user.id, name="المرونة", rollover_enabled=True)
    debt_env = Envelope(user_id=user.id, name="الديون", rollover_enabled=True)
    goal_env = Envelope(user_id=user.id, name="Main Goal", is_goal=True, rollover_enabled=True)

    db_session.add(fixed_env)
    db_session.add(flex_env)
    db_session.add(debt_env)
    db_session.add(goal_env)
    await db_session.flush()

    goal = Goal(
        user_id=user.id,
        envelope_id=goal_env.id,
        name="Main Goal",
        goal_type="goal",
        target_amount=Decimal("1000.00"),
        contribution_amount=Decimal("100.00"),
    )
    db_session.add(goal)
    await db_session.flush()

    # 4. Create rules matching standard configuration (Fixed, Flex, Debts, Goals)
    rule_fixed = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=fixed_env.id, mode="fixed_per_period", amount=Decimal("1500.00"), rank=1, enabled=True, auto_apply_on_income=True
    )
    rule_flex = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=flex_env.id, mode="fixed_per_period", amount=Decimal("1000.00"), rank=2, enabled=True, auto_apply_on_income=True
    )
    rule_debt = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=debt_env.id, mode="fixed_per_period", amount=Decimal("1500.00"), rank=3, enabled=True, auto_apply_on_income=True
    )
    rule_goal = DistributionRule(
        user_id=user.id, target_type="goal", target_id=goal.id, mode="fixed_per_period", amount=Decimal("1000.00"), rank=4, enabled=True, auto_apply_on_income=True
    )

    db_session.add(rule_fixed)
    db_session.add(rule_flex)
    db_session.add(rule_debt)
    db_session.add(rule_goal)
    await db_session.flush()

    ctx = DistributionContext(
        occurred_on=date.today(),
        period_start=date.today(),
        period_end=date.today() + timedelta(days=30),
    )

    # Standard distribution: 5000 MAD. Everyone gets their share.
    rules = [rule_fixed, rule_flex, rule_debt, rule_goal]
    standard_plan = await build_distribution_plan(
        db_session, user, ctx, rules, cash_available=Decimal("5000.00"), base_amount=Decimal("5000.00")
    )
    print("PLAN DETAILS:", [(x.target_name, x.amount) for x in standard_plan])
    assert len(standard_plan) == 4
    amounts_by_name = {item.target_name: item.amount for item in standard_plan}
    assert amounts_by_name["loyer"] == Decimal("1500.00")
    assert amounts_by_name["المرونه"] == Decimal("1000.00")
    assert amounts_by_name["الديون"] == Decimal("1500.00")
    assert amounts_by_name["Main Goal"] == Decimal("1000.00")

    # Deficit Scenario A: Actual income is 3500 MAD (deficit of 1500 MAD)
    # Target distribution: Fixed protected (1500), Debt protected (1500), remaining 500 to Flex. Goal gets 0.
    deficit_plan_a = await build_distribution_plan(
        db_session, user, ctx, rules, cash_available=Decimal("3500.00"), base_amount=Decimal("3500.00")
    )
    amounts_a = {item.target_name: item.amount for item in deficit_plan_a}
    assert amounts_a.get("loyer", Decimal("0")) == Decimal("1500.00")
    assert amounts_a.get("المرونه", Decimal("0")) == Decimal("500.00")
    assert amounts_a.get("الديون", Decimal("0")) == Decimal("1500.00")
    assert "Main Goal" not in amounts_a

    # Deficit Scenario B: Actual income is 2000 MAD (deficit of 3000 MAD)
    # Target distribution: Fixed gets 1500, Debt gets remaining 500. Flex and Goals get 0.
    deficit_plan_b = await build_distribution_plan(
        db_session, user, ctx, rules, cash_available=Decimal("2000.00"), base_amount=Decimal("2000.00")
    )
    amounts_b = {item.target_name: item.amount for item in deficit_plan_b}
    assert amounts_b.get("loyer", Decimal("0")) == Decimal("1500.00")
    assert amounts_b.get("الديون", Decimal("0")) == Decimal("500.00")
    assert "المرونه" not in amounts_b
    assert "Main Goal" not in amounts_b


@pytest.mark.asyncio
async def test_deficit_distribution_reversed_ranks(db_session: AsyncSession) -> None:
    # 1. Create User
    user = User(
        email=f"deficit-test-reversed-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()

    # 2. Add Onboarding record with expected_income_amount
    record = OnboardingV2Record(
        user_id=user.id,
        payload={
            "answers": {
                "Q0_income_type": "salaried",
                "S3_frequency": "monthly",
            },
            "draft_objects": {
                "sweep_bootstrap_v1": {
                    "last_income_date": "2026-06-01",
                    "last_income_amount": "5000",
                    "expected_income_amount": "5000",
                }
            }
        }
    )
    db_session.add(record)
    await db_session.flush()

    # 3. Create envelopes and goals
    # Note: Loyer is fixed, المرونة is flex, الديون is debt, Main Goal is goal.
    fixed_env = Envelope(user_id=user.id, name="Loyer", rollover_enabled=False)
    flex_env = Envelope(user_id=user.id, name="المرونة", rollover_enabled=True)
    debt_env = Envelope(user_id=user.id, name="الديون", rollover_enabled=True)
    goal_env = Envelope(user_id=user.id, name="Main Goal", is_goal=True, rollover_enabled=True)

    db_session.add(fixed_env)
    db_session.add(flex_env)
    db_session.add(debt_env)
    db_session.add(goal_env)
    await db_session.flush()

    goal = Goal(
        user_id=user.id,
        envelope_id=goal_env.id,
        name="Main Goal",
        goal_type="goal",
        target_amount=Decimal("1000.00"),
        contribution_amount=Decimal("100.00"),
    )
    db_session.add(goal)
    await db_session.flush()

    # 4. Create rules with REVERSED ranks (Goal is rank 1, Debt rank 2, Flex rank 3, Fixed rank 4)
    rule_goal = DistributionRule(
        user_id=user.id, target_type="goal", target_id=goal.id, mode="fixed_per_period", amount=Decimal("1000.00"), rank=1, enabled=True, auto_apply_on_income=True
    )
    rule_debt = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=debt_env.id, mode="fixed_per_period", amount=Decimal("1500.00"), rank=2, enabled=True, auto_apply_on_income=True
    )
    rule_flex = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=flex_env.id, mode="fixed_per_period", amount=Decimal("1000.00"), rank=3, enabled=True, auto_apply_on_income=True
    )
    rule_fixed = DistributionRule(
        user_id=user.id, target_type="envelope", target_id=fixed_env.id, mode="fixed_per_period", amount=Decimal("1500.00"), rank=4, enabled=True, auto_apply_on_income=True
    )

    db_session.add(rule_goal)
    db_session.add(rule_debt)
    db_session.add(rule_flex)
    db_session.add(rule_fixed)
    await db_session.flush()

    ctx = DistributionContext(
        occurred_on=date.today(),
        period_start=date.today(),
        period_end=date.today() + timedelta(days=30),
    )

    # 5. Run deficit scenario: Actual income is 3500 MAD (deficit of 1500 MAD)
    # Expected priorities: Fixed (1500) gets full, Debt (1500) gets full, Flex gets remaining 500. Goal gets 0.
    rules = [rule_goal, rule_debt, rule_flex, rule_fixed]
    deficit_plan = await build_distribution_plan(
        db_session, user, ctx, rules, cash_available=Decimal("3500.00"), base_amount=Decimal("3500.00")
    )
    amounts = {item.target_name: item.amount for item in deficit_plan}
    assert amounts.get("loyer", Decimal("0")) == Decimal("1500.00")
    assert amounts.get("المرونه", Decimal("0")) == Decimal("500.00")
    assert amounts.get("الديون", Decimal("0")) == Decimal("1500.00")
    assert "Main Goal" not in amounts

