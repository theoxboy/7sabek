from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_decision import AdvisorDecision


class AdvisorDecisionRepository:
    """Persistence access for advisor decisions."""

    async def get_by_decision_id(self, db: AsyncSession, decision_id: str) -> Optional[AdvisorDecision]:
        result = await db.execute(select(AdvisorDecision).where(AdvisorDecision.decision_id == decision_id))
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, entity: AdvisorDecision) -> AdvisorDecision:
        db.add(entity)
        await db.flush()
        return entity

    async def find_existing_accept(self, db: AsyncSession, user_id, preview_id: str, proposal_id: str, validation_id: str) -> Optional[AdvisorDecision]:
        result = await db.execute(
            select(AdvisorDecision).where(
                AdvisorDecision.user_id == user_id,
                AdvisorDecision.preview_id == preview_id,
                AdvisorDecision.proposal_id == proposal_id,
                AdvisorDecision.validation_id == validation_id,
            )
        )
        return result.scalar_one_or_none()
