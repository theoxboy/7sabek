from __future__ import annotations

from app.services.sweep_context import extract_sweep_bootstrap


def _extract_last_income_amount(raw_amount: str) -> str | None:
    bootstrap = extract_sweep_bootstrap(
        answers={
            "SWP1_last_income_date": "2026-04-26",
            "SWP2_last_income_amount": raw_amount,
        },
        draft_objects={},
    )
    if bootstrap is None:
        return None
    return bootstrap.get("last_income_amount")


def test_extract_sweep_bootstrap_parses_grouped_dot_amount() -> None:
    assert _extract_last_income_amount("8.800") == "8800.00"


def test_extract_sweep_bootstrap_parses_grouped_comma_amount() -> None:
    assert _extract_last_income_amount("8,800") == "8800.00"


def test_extract_sweep_bootstrap_parses_plain_amount() -> None:
    assert _extract_last_income_amount("8800") == "8800.00"


import pytest
from datetime import date
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import User, OnboardingV2Record
from app.services.sweep_context import resolve_user_sweep_anchor_date


@pytest.fixture()
async def db_session(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_user_sweep_anchor_date_prioritizes_bootstrap(db_session: AsyncSession) -> None:
    # 1. Create a user
    user = User(
        email=f"sweep-anchor-test-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()

    # 2. Create onboarding record with bootstrap last_income_date
    record = OnboardingV2Record(
        user_id=user.id,
        payload={
            "answers": {
                "Q0_income_type": "salaried",
                "S3_frequency": "monthly",
                "SWP1_last_income_date": "2026-05-25",
                "SWP2_last_income_amount": "8000"
            },
            "draft_objects": {
                "sweep_bootstrap_v1": {
                    "last_income_date": "2026-05-25",
                    "last_income_amount": "8000"
                }
            }
        }
    )
    db_session.add(record)
    await db_session.flush()

    # 3. Resolve anchor date
    anchor_date = await resolve_user_sweep_anchor_date(db_session, user)
    assert anchor_date == date(2026, 5, 25)

    # 4. Check that record now has cached sweep_anchor_date
    await db_session.refresh(record)
    assert record.payload.get("sweep_anchor_date") == "2026-05-25"


