from __future__ import annotations

from datetime import date as _date

from app.services.sweep_context import (
    clamp_anchor_to_fixed_pay_day,
    extract_sweep_bootstrap,
)


def test_clamp_anchor_snaps_to_declared_fixed_pay_day() -> None:
    assert clamp_anchor_to_fixed_pay_day(
        _date(2026, 7, 28), {"S4a_fixed_day": "31"}, 30
    ) == _date(2026, 7, 31)


def test_clamp_anchor_clamps_fixed_day_to_short_month() -> None:
    assert clamp_anchor_to_fixed_pay_day(
        _date(2026, 2, 20), {"M2b_monthly_fixed_day": "31"}, 30
    ) == _date(2026, 2, 28)


def test_clamp_anchor_is_noop_for_non_monthly_cadence() -> None:
    assert clamp_anchor_to_fixed_pay_day(
        _date(2026, 7, 28), {"S4a_fixed_day": "31"}, 7
    ) == _date(2026, 7, 28)


def test_clamp_anchor_is_noop_without_a_fixed_day_or_on_bad_input() -> None:
    assert clamp_anchor_to_fixed_pay_day(_date(2026, 7, 28), {}, 30) == _date(2026, 7, 28)
    assert clamp_anchor_to_fixed_pay_day(
        _date(2026, 7, 28), {"S4a_fixed_day": "abc"}, 30
    ) == _date(2026, 7, 28)
    assert clamp_anchor_to_fixed_pay_day(
        _date(2026, 7, 28), {"S4a_fixed_day": "99"}, 30
    ) == _date(2026, 7, 28)


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


@pytest.mark.asyncio
async def test_resolve_user_sweep_anchor_date_snaps_to_fixed_pay_day(
    db_session: AsyncSession,
) -> None:
    user = User(
        email=f"sweep-anchor-fixed-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db_session.add(user)
    await db_session.flush()

    record = OnboardingV2Record(
        user_id=user.id,
        payload={
            "answers": {
                "Q0_income_type": "salaried",
                "S3_frequency": "monthly",
                "S4a_fixed_day": "31",
                "SWP1_last_income_date": "2026-05-28",
            },
            "draft_objects": {
                "sweep_bootstrap_v1": {"last_income_date": "2026-05-28"}
            },
        },
    )
    db_session.add(record)
    await db_session.flush()

    # last income declared on the 28th, but the fixed pay day is the 31st.
    anchor_date = await resolve_user_sweep_anchor_date(db_session, user)
    assert anchor_date == date(2026, 5, 31)
    await db_session.refresh(record)
    assert record.payload.get("sweep_anchor_date") == "2026-05-31"




@pytest.mark.asyncio
async def test_resolve_anchor_pins_signup_date_when_no_last_income_and_never_shifts(
    db_session: AsyncSession,
) -> None:
    """No SWP1 date declared: the anchor is pinned to the signup date (clamped
    to the fixed pay day) and cached, so declaring the first income later does
    NOT shift the period grid."""
    from datetime import datetime, timezone
    from app.models import Category, Transaction, TransactionType

    user = User(
        email=f"sweep-anchor-nopay-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
        created_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()

    record = OnboardingV2Record(
        user_id=user.id,
        payload={
            "answers": {
                "Q0_income_type": "salaried",
                "S3_frequency": "monthly",
                "S4a_fixed_day": "28",
                # no SWP1_last_income_date
                "SWP2_last_income_amount": "5000",
            },
            "draft_objects": {"sweep_bootstrap_v1": {"last_income_amount": "5000"}},
        },
    )
    db_session.add(record)
    await db_session.flush()

    anchor = await resolve_user_sweep_anchor_date(db_session, user)
    assert anchor == date(2026, 5, 28)  # signup month, snapped to fixed pay day
    await db_session.refresh(record)
    assert record.payload.get("sweep_anchor_date") == "2026-05-28"

    # First manual income, declared on a different day.
    cat = Category(user_id=user.id, name="income_general")
    db_session.add(cat)
    await db_session.flush()
    db_session.add(
        Transaction(
            user_id=user.id,
            category_id=cat.id,
            type=TransactionType.INCOME,
            amount=5000,
            occurred_on=date(2026, 6, 15),
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
    )
    await db_session.flush()

    # Anchor must be unchanged (served from cache), not the 15th.
    anchor_again = await resolve_user_sweep_anchor_date(db_session, user)
    assert anchor_again == date(2026, 5, 28)
