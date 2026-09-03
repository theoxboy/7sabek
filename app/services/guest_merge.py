"""
Merge a guest's tracked expenses into an existing account.

We do NOT hand-move foreign keys (periods, movements, balances are fragile).
Instead we *replay* the guest's expense transactions onto the target account
through the same, tested `create_transaction_with_effects` path — the money and
the dates carry over, the account's own envelope/period machinery stays
consistent. Then the guest row is hard-deleted.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    CategoryEnvelopeMap,
    Envelope,
    Transaction,
    User,
)
from app.models.transaction import TransactionType
from app.services.envelope_rules import name_key
from app.services.transactions import (
    create_transaction_with_effects,
    resolve_cash_envelope,
)


async def _target_fallback_envelope_id(db: AsyncSession, target_id) -> str:
    """A "Divers"-ish envelope on the target, else its cash envelope."""
    rows = await db.execute(
        select(Envelope).where(Envelope.user_id == target_id)
    )
    envelopes = list(rows.scalars().all())
    for env in envelopes:
        if name_key(env.name) in {"divers", "miscellaneous", "autres"}:
            return env.id
    cash = await resolve_cash_envelope(db, target_id)
    return cash.id


async def merge_guest_into_account(
    db: AsyncSession, guest: User, target: User
) -> dict:
    guest_txns = list(
        (
            await db.execute(
                select(Transaction)
                .where(
                    Transaction.user_id == guest.id,
                    Transaction.type == TransactionType.EXPENSE,
                )
                .order_by(Transaction.occurred_on, Transaction.created_at)
            )
        )
        .scalars()
        .all()
    )

    guest_cat_name = {
        c.id: c.name
        for c in (
            await db.execute(select(Category).where(Category.user_id == guest.id))
        )
        .scalars()
        .all()
    }
    target_cat_by_key: dict[str, Category] = {
        name_key(c.name): c
        for c in (
            await db.execute(select(Category).where(Category.user_id == target.id))
        )
        .scalars()
        .all()
    }
    mapped_cat_ids = {
        row
        for row in (
            await db.execute(
                select(CategoryEnvelopeMap.category_id).where(
                    CategoryEnvelopeMap.user_id == target.id
                )
            )
        )
        .scalars()
        .all()
    }

    fallback_env_id = await _target_fallback_envelope_id(db, target.id)
    merged = 0

    for txn in guest_txns:
        raw_name = guest_cat_name.get(txn.category_id) or "miscellaneous"
        key = name_key(raw_name)
        target_cat = target_cat_by_key.get(key)
        if target_cat is None:
            target_cat = Category(user_id=target.id, name=raw_name)
            db.add(target_cat)
            await db.flush()
            target_cat_by_key[key] = target_cat
        if target_cat.id not in mapped_cat_ids:
            db.add(
                CategoryEnvelopeMap(
                    user_id=target.id,
                    category_id=target_cat.id,
                    envelope_id=fallback_env_id,
                )
            )
            mapped_cat_ids.add(target_cat.id)
            await db.flush()

        await create_transaction_with_effects(
            db,
            target,
            target_cat,
            TransactionType.EXPENSE,
            txn.amount,
            txn.occurred_on,
            txn.description,
            source="guest_merge",
            commit=False,
            enforce_auto_distribution_flag=False,
        )
        merged += 1

    return {"transactions_merged": merged}
