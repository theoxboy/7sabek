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
            assert "netflix" in names
            assert "aide famille" in names

    asyncio.run(_run())


def test_apply_funds_transport_leaf_envelopes_not_a_domain_aggregate(
    database_url: str,
) -> None:
    """Reproduces the /envelopes bug: a car user with fuel/insurance/maintenance/
    tax and E11 leaf envelopes must NOT end up with a stray "Transport" envelope
    holding all the money while the leaves stay at zero."""
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["E4_transport_mode"] = "car"
    answers.pop("TRP1_public_monthly_amount", None)
    answers["TR1_car_fuel_amount"] = "900"
    answers["TR1_car_maintenance_amount"] = "300"
    answers["TR1_car_insurance_amount"] = "400"
    answers["TR1_car_insurance_cycle"] = "monthly"
    answers["TR1_car_tax_annual_amount"] = "1200"
    answers["E6_support_family"] = "yes"
    answers["E6a_support_family_amount"] = "500"
    answers["E6b_support_family_cadence"] = "monthly"
    answers["E11_selected_envelopes_v1"] = [
        *[
            e
            for e in answers["E11_selected_envelopes_v1"]
            if e["name"] != "Transport"
        ],
        *[
            {
                "name": leaf,
                "final_name": leaf,
                "group_key": "transport",
                "final_rollover_enabled": True,
                "custom_category": None,
                "custom_amount": None,
            }
            for leaf in ("Carburant", "Assurance auto", "Entretien auto", "Taxe auto")
        ],
        {
            "name": "Famille — Aide",
            "final_name": "Famille — Aide",
            "group_key": "family",
            "final_rollover_enabled": True,
            "custom_category": None,
            "custom_amount": None,
        },
    ]

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _create_user_with_defaults(db, "apply-transport-leaves@example.com")
            await apply_onboarding_v2_payload(db, user, answers=answers, draft_objects={})
            await db.flush()

            from app.models import DistributionRule
            from app.services.distribution_name_normalization import (
                distribution_name_equivalent_key,
            )

            env_res = await db.execute(select(Envelope).where(Envelope.user_id == user.id))
            envs = list(env_res.scalars().all())
            by_lower = {e.name.strip().lower(): e for e in envs}

            # No "Transport" domain aggregate materialized.
            assert not any(
                distribution_name_equivalent_key(e.name) == "transport" for e in envs
            ), [e.name for e in envs]

            # One family envelope, not two.
            family = [
                e for e in envs if distribution_name_equivalent_key(e.name) == "family_aid"
            ]
            assert len(family) == 1, [e.name for e in family]

            rule_res = await db.execute(
                select(DistributionRule).where(DistributionRule.user_id == user.id)
            )
            rules_by_env = {r.target_id: r for r in rule_res.scalars().all()}

            # Each transport leaf carries its own fixed rule with a positive amount.
            for leaf in ("carburant", "assurance auto", "entretien auto", "taxe auto"):
                env = by_lower.get(leaf)
                assert env is not None, f"leaf envelope {leaf!r} not created: {list(by_lower)}"
                rule = rules_by_env.get(env.id)
                assert rule is not None, f"no rule for {leaf}"
                assert rule.mode == "fixed_per_period"
                assert float(rule.amount or 0) > 0, f"{leaf} rule amount = {rule.amount}"

            # And the family fixed rule attached to the single family envelope.
            assert rules_by_env.get(family[0].id) is not None

    asyncio.run(_run())


def test_apply_does_not_duplicate_family_aid_envelope_across_name_variants(
    database_url: str,
) -> None:
    """E11 sends "Famille — Aide"; the normalized fixed-expense feed references
    "Aide famille". They are the same envelope - apply must not create both, and
    the family distribution rule must still attach."""
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["E6_support_family"] = "yes"
    answers["E6a_support_family_amount"] = "400"
    answers["E6b_support_family_cadence"] = "monthly"
    answers["E11_selected_envelopes_v1"] = [
        *answers["E11_selected_envelopes_v1"],
        {
            "name": "Famille — Aide",
            "final_name": "Famille — Aide",
            "group_key": "family",
            "final_rollover_enabled": True,
            "custom_category": None,
            "custom_amount": None,
        },
    ]

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _create_user_with_defaults(db, "apply-family-variant@example.com")
            await apply_onboarding_v2_payload(db, user, answers=answers, draft_objects={})
            await db.flush()

            from app.models import DistributionRule
            from app.services.distribution_name_normalization import (
                distribution_name_equivalent_key,
            )

            env_res = await db.execute(select(Envelope).where(Envelope.user_id == user.id))
            family_envs = [
                e
                for e in env_res.scalars().all()
                if distribution_name_equivalent_key(e.name) == "family_aid"
            ]
            assert len(family_envs) == 1, [e.name for e in family_envs]

            rule_res = await db.execute(
                select(DistributionRule).where(
                    DistributionRule.user_id == user.id,
                    DistributionRule.target_id == family_envs[0].id,
                )
            )
            assert rule_res.scalar_one_or_none() is not None

    asyncio.run(_run())


def test_apply_onboarding_v2_payload_injects_no_starting_balance(database_url: str) -> None:
    """Onboarding never seeds a synthetic income - the first real income is
    declared manually by the user afterwards, and that is what seeds cash and
    runs the initial distribution. Re-applying onboarding is therefore a no-op
    for transactions (no duplicated "Starting Balance")."""
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["SWP2_last_income_amount"] = "4200"

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _create_user_with_defaults(db, "apply-no-starting-balance@example.com")

            for _ in range(2):  # applying twice must not create anything
                await apply_onboarding_v2_payload(
                    db, user, answers=answers, draft_objects={}
                )
                await db.flush()

            from app.models import Transaction, EnvelopeMovement

            tx_res = await db.execute(
                select(Transaction).where(Transaction.user_id == user.id)
            )
            assert list(tx_res.scalars().all()) == []

            m_res = await db.execute(
                select(EnvelopeMovement).where(EnvelopeMovement.user_id == user.id)
            )
            assert list(m_res.scalars().all()) == []

    asyncio.run(_run())

