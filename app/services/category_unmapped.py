from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, CategoryEnvelopeMap
from app.services.category_catalog import (
    EXPENSE_CATEGORY_KEYS_SQL,
    category_key_from_name,
    is_internal_income_category_key,
)

_SYSTEM_EXPENSE_KEYS = set(EXPENSE_CATEGORY_KEYS_SQL)


def _is_system_expense_category(name: str) -> bool:
    return category_key_from_name(name) in _SYSTEM_EXPENSE_KEYS


async def list_manual_unmapped_categories(
    db: AsyncSession, user_id: UUID
) -> list[Category]:
    result = await db.execute(
        select(Category)
        .outerjoin(
            CategoryEnvelopeMap,
            (CategoryEnvelopeMap.category_id == Category.id)
            & (CategoryEnvelopeMap.user_id == user_id),
        )
        .where(
            Category.user_id == user_id,
            CategoryEnvelopeMap.id.is_(None),
        )
    )
    categories = list(result.scalars().all())
    return [
        category
        for category in categories
        if not is_internal_income_category_key(category.name)
        and not _is_system_expense_category(category.name)
    ]


async def count_manual_unmapped_categories(db: AsyncSession, user_id: UUID) -> int:
    categories = await list_manual_unmapped_categories(db, user_id)
    return len(categories)

