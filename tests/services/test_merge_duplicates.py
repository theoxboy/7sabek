from __future__ import annotations

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4, UUID
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import (
    User,
    Envelope,
    EnvelopePeriod,
    EnvelopeMovement,
    EnvelopeAllocation,
    Category,
    CategoryEnvelopeMap,
    Goal,
    EnvelopeAdjustmentLog,
    EnvelopeTransferLog,
)
from scripts.force_merge_arabic_duplicates import merge_user_envelopes


@pytest.fixture()
async def db_session(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_duplicate_envelopes(db_session: AsyncSession) -> None:
    # 1. Create User
    user = User(
        email=f"merge-test-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()

    # 2. Insert duplicate envelopes directly using SQL to bypass @validates
    # One is normalized ("الماكله"), one is not ("الماكلة")
    env_target_id = uuid4()
    env_dup_id = uuid4()

    await db_session.execute(
        text(
            "INSERT INTO envelopes (id, user_id, name, rollover_enabled, is_default_savings, is_cash, is_goal, deletable) "
            "VALUES (:id, :user_id, :name, true, false, false, false, true)"
        ),
        {"id": env_target_id, "user_id": user.id, "name": "الماكله"},
    )
    await db_session.execute(
        text(
            "INSERT INTO envelopes (id, user_id, name, rollover_enabled, is_default_savings, is_cash, is_goal, deletable) "
            "VALUES (:id, :user_id, :name, true, false, false, false, true)"
        ),
        {"id": env_dup_id, "user_id": user.id, "name": "الماكلة"},
    )
    await db_session.flush()

    # 3. Create periods for both envelopes
    period_start = date.today()
    period_end = date.today() + timedelta(days=30)
    
    period_target = EnvelopePeriod(
        user_id=user.id,
        envelope_id=env_target_id,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0.00"),
    )
    period_dup = EnvelopePeriod(
        user_id=user.id,
        envelope_id=env_dup_id,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0.00"),
    )
    db_session.add(period_target)
    db_session.add(period_dup)
    await db_session.flush()

    # 4. Add transactions, allocations, and logs to the duplicate envelope/period
    category = Category(user_id=user.id, name="Food Category")
    db_session.add(category)
    await db_session.flush()

    mapping = CategoryEnvelopeMap(
        user_id=user.id,
        category_id=category.id,
        envelope_id=env_dup_id,
    )
    db_session.add(mapping)

    allocation = EnvelopeAllocation(
        user_id=user.id,
        envelope_period_id=period_dup.id,
        amount=Decimal("500.00"),
    )
    db_session.add(allocation)

    movement = EnvelopeMovement(
        user_id=user.id,
        envelope_period_id=period_dup.id,
        amount=Decimal("-150.00"),
    )
    db_session.add(movement)

    adj_log = EnvelopeAdjustmentLog(
        user_id=user.id,
        envelope_id=env_dup_id,
        period_start=period_start,
        period_end=period_end,
        previous_balance=Decimal("0.00"),
        new_balance=Decimal("350.00"),
        delta=Decimal("350.00"),
    )
    db_session.add(adj_log)
    await db_session.flush()

    # 5. Execute merge
    await merge_user_envelopes(db_session, user)

    # 6. Verify duplicates are removed and all entities are merged
    # Check envelopes count
    envs_res = await db_session.execute(
        select(Envelope).where(Envelope.user_id == user.id)
    )
    envs = list(envs_res.scalars().all())
    assert len(envs) == 1
    surviving_id = envs[0].id
    assert surviving_id in (env_target_id, env_dup_id)
    assert envs[0].name == "الماكله"

    # Find the surviving period
    periods_res = await db_session.execute(
        select(EnvelopePeriod).where(EnvelopePeriod.user_id == user.id)
    )
    periods = list(periods_res.scalars().all())
    assert len(periods) == 1
    surviving_period_id = periods[0].id

    # Check mapping reassigns to target envelope
    mapping_res = await db_session.execute(
        select(CategoryEnvelopeMap).where(CategoryEnvelopeMap.user_id == user.id)
    )
    mapping_after = mapping_res.scalar_one()
    assert mapping_after.envelope_id == surviving_id

    # Check allocation & movement are migrated to target period
    alloc_res = await db_session.execute(
        select(EnvelopeAllocation).where(EnvelopeAllocation.user_id == user.id)
    )
    alloc_after = alloc_res.scalar_one()
    assert alloc_after.envelope_period_id == surviving_period_id

    move_res = await db_session.execute(
        select(EnvelopeMovement).where(EnvelopeMovement.user_id == user.id)
    )
    move_after = move_res.scalar_one()
    assert move_after.envelope_period_id == surviving_period_id

    # Check adjustment logs reassigned to target envelope
    adj_res = await db_session.execute(
        select(EnvelopeAdjustmentLog).where(EnvelopeAdjustmentLog.user_id == user.id)
    )
    adj_after = adj_res.scalar_one()
    assert adj_after.envelope_id == surviving_id
