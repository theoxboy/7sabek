from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select, update

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db.session import get_sessionmaker
from app.models import Category, CategoryEnvelopeMap, Transaction, User
from app.services.category_catalog import (
    EXPENSE_CATEGORY_CATALOG,
    INTERNAL_INCOME_CATEGORY_KEY,
    category_key_from_name,
    is_internal_income_category_key,
)
from app.services.category_auto_mapping import (
    fill_missing_category_mappings_for_user,
    reconcile_category_mappings_for_user,
)


@dataclass
class RetrofitStats:
    users_scanned: int = 0
    categories_renamed: int = 0
    categories_created_missing: int = 0
    categories_merged: int = 0
    mappings_deleted: int = 0
    transactions_reassigned: int = 0
    income_categories_created: int = 0
    mappings_auto_created: int = 0
    mappings_auto_updated: int = 0
    mappings_auto_deleted: int = 0


async def _retrofit_user_categories(session, user_id: UUID, stats: RetrofitStats) -> None:
    result = await session.execute(
        select(Category)
        .where(Category.user_id == user_id)
        .order_by(Category.created_at.asc(), Category.id.asc())
    )
    categories = list(result.scalars().all())
    if not categories:
        return

    for category in categories:
        canonical = category_key_from_name(category.name)
        if canonical and canonical != category.name:
            category.name = canonical
            stats.categories_renamed += 1

    by_key: dict[str, list[Category]] = defaultdict(list)
    for category in categories:
        by_key[category.name].append(category)

    for key, duplicates in by_key.items():
        if len(duplicates) <= 1:
            continue

        survivor = duplicates[0]
        for duplicate in duplicates[1:]:
            tx_update = await session.execute(
                update(Transaction)
                .where(
                    Transaction.user_id == user_id,
                    Transaction.category_id == duplicate.id,
                )
                .values(category_id=survivor.id)
            )
            stats.transactions_reassigned += int(tx_update.rowcount or 0)

            survivor_mapping_result = await session.execute(
                select(CategoryEnvelopeMap).where(
                    CategoryEnvelopeMap.user_id == user_id,
                    CategoryEnvelopeMap.category_id == survivor.id,
                )
            )
            survivor_mapping = survivor_mapping_result.scalar_one_or_none()

            duplicate_mapping_result = await session.execute(
                select(CategoryEnvelopeMap).where(
                    CategoryEnvelopeMap.user_id == user_id,
                    CategoryEnvelopeMap.category_id == duplicate.id,
                )
            )
            duplicate_mapping = duplicate_mapping_result.scalar_one_or_none()

            if duplicate_mapping is not None:
                if survivor_mapping is None:
                    duplicate_mapping.category_id = survivor.id
                else:
                    await session.execute(
                        delete(CategoryEnvelopeMap).where(
                            CategoryEnvelopeMap.id == duplicate_mapping.id
                        )
                    )
                    stats.mappings_deleted += 1

            await session.delete(duplicate)
            stats.categories_merged += 1

    refreshed_result = await session.execute(
        select(Category).where(Category.user_id == user_id)
    )
    refreshed_categories = list(refreshed_result.scalars().all())
    existing_names = {category.name for category in refreshed_categories}
    for entry in EXPENSE_CATEGORY_CATALOG:
        if entry.key in existing_names:
            continue
        session.add(Category(user_id=user_id, name=entry.key))
        stats.categories_created_missing += 1
        existing_names.add(entry.key)

    # Keep one hidden internal income category for technical consistency.
    income_category = None
    for category in categories:
        if is_internal_income_category_key(category.name):
            income_category = category
            break
    if income_category is None:
        income_category = Category(user_id=user_id, name=INTERNAL_INCOME_CATEGORY_KEY)
        session.add(income_category)
        stats.income_categories_created += 1
        await session.flush()

    # Force all income transactions to use the internal income category.
    income_tx_update = await session.execute(
        update(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "income",
            Transaction.category_id != income_category.id,
        )
        .values(category_id=income_category.id)
    )
    stats.transactions_reassigned += int(income_tx_update.rowcount or 0)

    # Income categories should not be mapped to envelopes.
    await session.execute(
        delete(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == user_id,
            CategoryEnvelopeMap.category_id.in_(
                select(Category.id).where(
                    Category.user_id == user_id,
                    Category.name == INTERNAL_INCOME_CATEGORY_KEY,
                )
            ),
        )
    )
    created = await fill_missing_category_mappings_for_user(
        session, user_id, allow_group_envelope_creation=True
    )
    stats.mappings_auto_created += created
    rec_created, rec_updated, rec_deleted = await reconcile_category_mappings_for_user(
        session, user_id, allow_group_envelope_creation=True
    )
    stats.mappings_auto_created += rec_created
    stats.mappings_auto_updated += rec_updated
    stats.mappings_auto_deleted += rec_deleted


async def retrofit_all_users(commit: bool) -> RetrofitStats:
    stats = RetrofitStats()
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        users_result = await session.execute(select(User.id))
        user_ids = [row[0] for row in users_result.all()]
        stats.users_scanned = len(user_ids)
        for user_id in user_ids:
            await _retrofit_user_categories(session, user_id, stats)

        if commit:
            await session.commit()
        else:
            await session.rollback()
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrofit categories to the new catalog keys for all users."
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist changes. Without this flag, run as dry-run and rollback.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = asyncio.run(retrofit_all_users(commit=args.commit))
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] users_scanned={stats.users_scanned}")
    print(f"[{mode}] categories_renamed={stats.categories_renamed}")
    print(f"[{mode}] categories_created_missing={stats.categories_created_missing}")
    print(f"[{mode}] categories_merged={stats.categories_merged}")
    print(f"[{mode}] mappings_deleted={stats.mappings_deleted}")
    print(f"[{mode}] transactions_reassigned={stats.transactions_reassigned}")
    print(f"[{mode}] income_categories_created={stats.income_categories_created}")
    print(f"[{mode}] mappings_auto_created={stats.mappings_auto_created}")
    print(f"[{mode}] mappings_auto_updated={stats.mappings_auto_updated}")
    print(f"[{mode}] mappings_auto_deleted={stats.mappings_auto_deleted}")


if __name__ == "__main__":
    main()
