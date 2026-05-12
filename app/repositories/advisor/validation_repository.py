from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_pre_apply_validation import AdvisorPreApplyValidation


class AdvisorValidationRepository:
    """Persistence access for advisor pre-apply validations."""

    async def get_by_validation_id(self, db: AsyncSession, validation_id: str) -> Optional[AdvisorPreApplyValidation]:
        result = await db.execute(
            select(AdvisorPreApplyValidation).where(AdvisorPreApplyValidation.validation_id == validation_id)
        )
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, entity: AdvisorPreApplyValidation) -> AdvisorPreApplyValidation:
        db.add(entity)
        await db.flush()
        return entity
