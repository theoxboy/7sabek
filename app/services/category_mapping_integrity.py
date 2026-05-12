from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category
from app.services.category_catalog import EXPENSE_CATEGORY_CATALOG, INTERNAL_INCOME_CATEGORY_KEY
from app.services.category_auto_mapping import (
    fill_missing_category_mappings_for_user,
    reconcile_category_mappings_for_user,
)
from app.services.category_eligibility import (
    eligible_expense_category_keys_from_answers,
    latest_onboarding_answers_for_user,
    migrate_transacted_ineligible_categories_for_user,
    prune_ineligible_categories_for_user,
)
from app.services.envelope_rules import normalize_name


async def ensure_system_category_mappings(
    db: AsyncSession, user_id: UUID, *, repair: bool = False
) -> tuple[int, int, int]:
    """
    Enforce mapping integrity for system categories.
    Returns: (created, updated, deleted)
    """
    if not repair:
        return (0, 0, 0)

    answers = await latest_onboarding_answers_for_user(db, user_id)
    eligible_keys = eligible_expense_category_keys_from_answers(answers)

    result = await db.execute(select(Category).where(Category.user_id == user_id))
    categories = list(result.scalars().all())
    existing_names = {normalize_name(category.name) for category in categories}

    seeded_categories = 0
    for entry in EXPENSE_CATEGORY_CATALOG:
        if entry.key not in eligible_keys:
            continue
        if normalize_name(entry.key) in existing_names:
            continue
        db.add(Category(user_id=user_id, name=entry.key))
        seeded_categories += 1

    if normalize_name(INTERNAL_INCOME_CATEGORY_KEY) not in existing_names:
        db.add(Category(user_id=user_id, name=INTERNAL_INCOME_CATEGORY_KEY))
        seeded_categories += 1

    pruned_categories = await prune_ineligible_categories_for_user(
        db,
        user_id,
        eligible_keys=eligible_keys,
    )
    migrated_transactions = await migrate_transacted_ineligible_categories_for_user(
        db,
        user_id,
        eligible_keys=eligible_keys,
    )

    created = await fill_missing_category_mappings_for_user(
        db,
        user_id,
        allow_group_envelope_creation=True,
    )
    rec_created, rec_updated, rec_deleted = await reconcile_category_mappings_for_user(
        db,
        user_id,
        allow_group_envelope_creation=True,
    )
    total_created = created + rec_created
    if (
        seeded_categories
        or pruned_categories
        or migrated_transactions
        or total_created
        or rec_updated
        or rec_deleted
    ):
        await db.commit()
    return total_created, rec_updated, rec_deleted
