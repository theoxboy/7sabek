from __future__ import annotations

from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Category, CategoryEnvelopeMap, Envelope, Transaction, User
from app.schemas.category import (
    CategoryCreate,
    CategoryEnvelopeMapOut,
    CategoryEnvelopeMapUpsert,
    CategoryOut,
    CategoryUpdate,
)
from app.services.envelope_rules import (
    is_category_mappable_envelope,
    name_key,
    normalize_name,
)
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
from app.services.category_mapping_integrity import ensure_system_category_mappings
from app.services.category_unmapped import (
    count_manual_unmapped_categories,
    list_manual_unmapped_categories,
)
from app.services.category_eligibility import (
    derive_profile_signals_from_answers,
    eligible_expense_category_keys_from_answers,
    latest_onboarding_answers_for_user,
    migrate_transacted_ineligible_categories_for_user,
    prune_ineligible_categories_for_user,
)
from app.services.gamification import award_fix_points_if_needed, to_local_date

router = APIRouter(prefix="/categories")


async def _find_category_name_conflict(
    db: AsyncSession,
    user_id,
    candidate_name: str,
    exclude_category_id: UUID | None = None,
) -> Category | None:
    result = await db.execute(select(Category).where(Category.user_id == user_id))
    candidate_key = name_key(candidate_name)
    for category in result.scalars().all():
        if exclude_category_id is not None and category.id == exclude_category_id:
            continue
        if name_key(category.name) == candidate_key:
            return category
    return None


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryOut:
    normalized_name = normalize_name(payload.name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="CATEGORY_NAME_REQUIRED")
    if (
        await _find_category_name_conflict(db, current_user.id, normalized_name)
    ) is not None:
        raise HTTPException(status_code=400, detail="CATEGORY_NAME_EXISTS")

    category = Category(user_id=current_user.id, name=normalized_name)
    db.add(category)
    await db.commit()
    await db.refresh(category)

    return category


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CategoryOut]:
    await ensure_system_category_mappings(db, current_user.id, repair=True)
    result = await db.execute(select(Category).where(Category.user_id == current_user.id))
    return list(result.scalars().all())


@router.get("/unmapped-manual", response_model=list[CategoryOut])
async def list_unmapped_manual_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CategoryOut]:
    await ensure_system_category_mappings(db, current_user.id, repair=True)
    return await list_manual_unmapped_categories(db, current_user.id)


@router.get("/eligibility-signals")
async def category_eligibility_signals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    answers = await latest_onboarding_answers_for_user(db, current_user.id)
    return derive_profile_signals_from_answers(answers)


@router.post("/self-heal")
async def self_heal_categories(
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    answers = await latest_onboarding_answers_for_user(db, current_user.id)
    eligible_keys = eligible_expense_category_keys_from_answers(answers)
    result = await db.execute(select(Category).where(Category.user_id == current_user.id))
    categories = list(result.scalars().all())
    existing_names = {normalize_name(category.name) for category in categories}

    missing_required = [
        entry.key
        for entry in EXPENSE_CATEGORY_CATALOG
        if entry.key in eligible_keys and normalize_name(entry.key) not in existing_names
    ]
    if normalize_name(INTERNAL_INCOME_CATEGORY_KEY) not in existing_names:
        missing_required.append(INTERNAL_INCOME_CATEGORY_KEY)

    created_categories = 0
    created_mappings = 0
    rec_created = 0
    rec_updated = 0
    rec_deleted = 0
    pruned_categories = 0
    migrated_transactions = 0

    if dry_run:
        savepoint = await db.begin_nested()
        try:
            pruned_categories = await prune_ineligible_categories_for_user(
                db,
                current_user.id,
                eligible_keys=eligible_keys,
            )
            migrated_transactions = await migrate_transacted_ineligible_categories_for_user(
                db,
                current_user.id,
                eligible_keys=eligible_keys,
            )
            if missing_required:
                for key in missing_required:
                    db.add(Category(user_id=current_user.id, name=key))
                    created_categories += 1
                await db.flush()
            created_mappings = await fill_missing_category_mappings_for_user(
                db,
                current_user.id,
                allow_group_envelope_creation=True,
            )
            rec_created, rec_updated, rec_deleted = await reconcile_category_mappings_for_user(
                db,
                current_user.id,
                allow_group_envelope_creation=True,
            )
        finally:
            await savepoint.rollback()
        return {
            "dry_run": 1,
            "categories_created": created_categories,
            "mappings_created": created_mappings + rec_created,
            "mappings_updated": rec_updated,
            "mappings_deleted": rec_deleted,
            "categories_pruned": pruned_categories,
            "transactions_migrated": migrated_transactions,
        }

    pruned_categories = await prune_ineligible_categories_for_user(
        db,
        current_user.id,
        eligible_keys=eligible_keys,
    )
    migrated_transactions = await migrate_transacted_ineligible_categories_for_user(
        db,
        current_user.id,
        eligible_keys=eligible_keys,
    )
    if missing_required:
        for key in missing_required:
            db.add(Category(user_id=current_user.id, name=key))
            created_categories += 1
        await db.flush()

    created_mappings = await fill_missing_category_mappings_for_user(
        db,
        current_user.id,
        allow_group_envelope_creation=True,
    )
    rec_created, rec_updated, rec_deleted = await reconcile_category_mappings_for_user(
        db,
        current_user.id,
        allow_group_envelope_creation=True,
    )

    if created_categories or created_mappings or rec_created or rec_updated or rec_deleted:
        await db.commit()
    elif pruned_categories:
        await db.commit()

    return {
        "dry_run": 0,
        "categories_created": created_categories,
        "mappings_created": created_mappings + rec_created,
        "mappings_updated": rec_updated,
        "mappings_deleted": rec_deleted,
        "categories_pruned": pruned_categories,
        "transactions_migrated": migrated_transactions,
    }


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryOut:
    result = await db.execute(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    category = result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    normalized_name = normalize_name(payload.name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="CATEGORY_NAME_REQUIRED")
    if (
        await _find_category_name_conflict(
            db, current_user.id, normalized_name, exclude_category_id=category.id
        )
    ) is not None:
        raise HTTPException(status_code=400, detail="CATEGORY_NAME_EXISTS")

    category.name = normalized_name
    await db.commit()
    await db.refresh(category)

    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    category = result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    tx_exists = await db.execute(
        select(Transaction).where(
            Transaction.user_id == current_user.id,
            Transaction.category_id == category.id,
        )
    )
    if tx_exists.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="CATEGORY_HAS_TRANSACTIONS")

    await db.execute(
        delete(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id,
            CategoryEnvelopeMap.category_id == category.id,
        )
    )

    await db.delete(category)
    await db.commit()


@router.put("/{category_id}/envelope", response_model=CategoryEnvelopeMapOut)
async def upsert_category_envelope_mapping(
    category_id: UUID,
    payload: CategoryEnvelopeMapUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryEnvelopeMapOut:
    unmapped_before = await count_manual_unmapped_categories(db, current_user.id)

    category_result = await db.execute(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    category = category_result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if is_internal_income_category_key(category.name):
        raise HTTPException(
            status_code=400,
            detail="CATEGORY_MAPPING_FOR_INTERNAL_INCOME_FORBIDDEN",
        )

    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.id == payload.envelope_id,
            Envelope.user_id == current_user.id,
        )
    )
    envelope = envelope_result.scalar_one_or_none()
    if envelope is None:
        raise HTTPException(status_code=404, detail="Envelope not found")
    if not is_category_mappable_envelope(envelope):
        if envelope.is_cash:
            raise HTTPException(status_code=400, detail="CATEGORY_MAPPING_TO_CASH_FORBIDDEN")
        if envelope.is_default_savings:
            raise HTTPException(
                status_code=400, detail="CATEGORY_MAPPING_TO_SAVINGS_FORBIDDEN"
            )
        if envelope.is_goal:
            raise HTTPException(status_code=400, detail="CATEGORY_MAPPING_TO_GOAL_FORBIDDEN")
        raise HTTPException(status_code=400, detail="CATEGORY_MAPPING_TARGET_INVALID")

    mapping_result = await db.execute(
        select(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id,
            CategoryEnvelopeMap.category_id == category.id,
        )
    )
    mapping = mapping_result.scalar_one_or_none()

    if mapping is None:
        mapping = CategoryEnvelopeMap(
            user_id=current_user.id,
            category_id=category.id,
            envelope_id=envelope.id,
        )
        db.add(mapping)
    else:
        mapping.envelope_id = envelope.id

    await db.commit()
    await db.refresh(mapping)

    unmapped_after = await count_manual_unmapped_categories(db, current_user.id)
    if unmapped_before > 0 and unmapped_after == 0:
        await award_fix_points_if_needed(
            db,
            current_user,
            to_local_date(datetime.now(timezone.utc)),
            event_type="fix_unmapped",
            points=30,
            meta={"from": unmapped_before, "to": unmapped_after},
        )
        await db.commit()

    return mapping


@router.delete("/{category_id}/envelope", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category_envelope_mapping(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    category_result = await db.execute(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    category = category_result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    await db.execute(
        delete(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id,
            CategoryEnvelopeMap.category_id == category.id,
        )
    )
    await db.commit()
