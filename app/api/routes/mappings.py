from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import CategoryEnvelopeMap, User
from app.schemas.category import CategoryEnvelopeMapOut
from app.services.category_mapping_integrity import ensure_system_category_mappings

router = APIRouter(prefix="/mappings")


@router.get("", response_model=list[CategoryEnvelopeMapOut])
async def list_mappings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CategoryEnvelopeMapOut]:
    await ensure_system_category_mappings(db, current_user.id, repair=True)
    result = await db.execute(
        select(CategoryEnvelopeMap).where(
            CategoryEnvelopeMap.user_id == current_user.id
        )
    )
    return list(result.scalars().all())
