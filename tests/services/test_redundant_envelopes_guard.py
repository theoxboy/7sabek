from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Envelope, User
from app.services.onboarding_v2_apply import apply_onboarding_v2_payload
from tests.onboarding_v2_apply_test_support import build_answers


async def _build_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _create_user(db: AsyncSession, email: str) -> User:
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


def test_redundant_generic_envelopes_are_not_materialized(database_url: str) -> None:
    """
    When specific funded envelopes exist (Loyer for housing, Internet/Téléphone for bills),
    generic unbudgeted duplicates (Charges, Factures, التوازن) must NOT be materialized in DB.
    """
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)

    # User has specific fixed expenses: Rent (Loyer) and Internet/Phone (no generic bills)
    answers["FX1_fixed_items"] = ["internet_phone"]
    answers["FX2_amount_internet_phone"] = "100"
    answers.pop("FX2_amount_bills", None)

    # Add both funded envelopes and unbudgeted duplicates
    answers["E11_selected_envelopes_v1"] = [
        {"name": "Loyer", "final_name": "Loyer", "group_key": "housing", "custom_amount": None},
        {"name": "Charges", "final_name": "Charges", "group_key": "housing", "custom_amount": None},
        {"name": "Internet/Téléphone", "final_name": "Internet/Téléphone", "group_key": "bills", "custom_amount": None},
        {"name": "Factures", "final_name": "Factures", "group_key": "bills", "custom_amount": None},
        {"name": "التوازن", "final_name": "التوازن", "group_key": "buffer", "custom_amount": None},
        {"name": "Transport", "final_name": "Transport", "group_key": "transport", "custom_amount": None},
        {"name": "Dettes — Visa", "final_name": "Dettes — Visa", "group_key": "debts", "custom_amount": None},
        {"name": "Objectif — Voyage", "final_name": "Objectif — Voyage", "group_key": "goals", "custom_amount": None},
    ]

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _create_user(db, "guard-test@example.com")
            summary = await apply_onboarding_v2_payload(
                db=db,
                user=user,
                answers=answers,
                draft_objects=None,
            )
            await db.commit()

            # Query all materialized envelopes for the user
            envelopes_res = await db.execute(
                select(Envelope.name).where(Envelope.user_id == user.id)
            )
            created_names = {row[0] for row in envelopes_res.fetchall()}

            # Loyer and Internet/Téléphone MUST exist
            assert "loyer" in {n.lower() for n in created_names}
            assert any("internet" in n.lower() or "téléphone" in n.lower() for n in created_names)

            # Redundant empty envelopes MUST NOT exist
            assert "charges" not in {n.lower() for n in created_names}
            assert "factures" not in {n.lower() for n in created_names}
            assert "التوازن" not in created_names

            # Verify category mappings route to the funded specific envelopes
            from app.models import Category, CategoryEnvelopeMap
            mappings_res = await db.execute(
                select(Category.name, Envelope.name)
                .join(CategoryEnvelopeMap, CategoryEnvelopeMap.category_id == Category.id)
                .join(Envelope, Envelope.id == CategoryEnvelopeMap.envelope_id)
                .where(Category.user_id == user.id)
            )
            cat_map = {row[0].lower(): row[1].lower() for row in mappings_res.fetchall()}
            assert cat_map.get("housing_generic") == "loyer"
            assert any(k in cat_map.get("bills_generic", "") for k in ("internet", "téléphone"))

    asyncio.run(_run())
