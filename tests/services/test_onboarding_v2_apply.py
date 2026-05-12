from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Envelope, User
from app.services.onboarding_v2_apply import apply_onboarding_v2_payload
from tests.onboarding_v2_apply_test_support import build_answers, draft_objects_garbage, serialize_user_state


async def _build_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _create_user_with_defaults(db: AsyncSession, email: str) -> User:
    user = User(
        email=email,
        password_hash="x",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date(2026, 4, 30),
        auto_distribution_enabled=False,
    )
    db.add(user)
    await db.flush()
    db.add(
        Envelope(
            user_id=user.id,
            name="Epargnes",
            is_default_savings=True,
            deletable=False,
            rollover_enabled=True,
        )
    )
    db.add(
        Envelope(
            user_id=user.id,
            name="Cash",
            is_cash=True,
            is_default_savings=False,
            deletable=False,
            rollover_enabled=False,
        )
    )
    await db.flush()
    return user


def test_apply_onboarding_v2_payload_is_independent_from_draft_objects(database_url: str) -> None:
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user_a = await _create_user_with_defaults(db, "apply-a@example.com")
            user_b = await _create_user_with_defaults(db, "apply-b@example.com")

            summary_a = await apply_onboarding_v2_payload(
                db,
                user_a,
                answers=answers,
                draft_objects=draft_objects_garbage(),
            )
            summary_b = await apply_onboarding_v2_payload(
                db,
                user_b,
                answers=answers,
                draft_objects={},
            )
            await db.flush()

            state_a = await serialize_user_state(db, user_a)
            state_b = await serialize_user_state(db, user_b)

            assert state_a == state_b
            assert summary_a["selected_envelopes_count"] == summary_b["selected_envelopes_count"] == 4
            assert summary_a["cashflow_remaining_monthly"] == summary_b["cashflow_remaining_monthly"] == 3400.0

    asyncio.run(_run())


def test_apply_materializes_expense_envelopes_missing_from_e11_selection(database_url: str) -> None:
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["FX1_fixed_items"] = ["bills", "other"]
    answers["FX2_amount_bills"] = "300"
    answers["FX3_other_fixed_rows"] = [{"name": "Netflix", "amount": 150, "cadence": "monthly"}]
    answers["E6_support_family"] = "yes"
    answers["E6a_support_family_amount"] = "400"
    answers["E6b_support_family_cadence"] = "monthly"

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _create_user_with_defaults(db, "apply-materialize-expense-envelopes@example.com")
            await apply_onboarding_v2_payload(
                db,
                user,
                answers=answers,
                draft_objects={},
            )
            await db.flush()

            envelopes_result = await db.execute(select(Envelope).where(Envelope.user_id == user.id))
            names = {env.name for env in envelopes_result.scalars().all()}
            assert "Netflix" in names
            assert "Aide famille" in names

    asyncio.run(_run())
