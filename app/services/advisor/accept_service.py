from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_decision import AdvisorDecision
from app.models.user import User
from app.repositories.advisor.decision_repository import AdvisorDecisionRepository
from app.repositories.advisor.validation_repository import AdvisorValidationRepository
from app.schemas.advisor.contracts import AdvisorDecisionV1


class AcceptService:
    """Records accepted advisor decision after successful pre-apply validation."""

    def __init__(
        self,
        decisions: AdvisorDecisionRepository,
        validations: AdvisorValidationRepository,
    ) -> None:
        self.decisions = decisions
        self.validations = validations

    async def accept(
        self,
        db: AsyncSession,
        user: User,
        preview_id: str,
        proposal_id: str,
        validation_id: str,
        confirm: bool,
    ) -> AdvisorDecisionV1:
        if not confirm:
            raise ValueError("ADVISOR_ACCEPT_CONFIRM_REQUIRED")

        existing = await self.decisions.find_existing_accept(
            db,
            user.id,
            preview_id,
            proposal_id,
            validation_id,
        )
        if existing is not None:
            return AdvisorDecisionV1.model_validate(existing.decision_payload)

        validation = await self.validations.get_by_validation_id(db, validation_id)
        if validation is None:
            raise ValueError("ADVISOR_PRE_APPLY_VALIDATION_NOT_FOUND")

        now = datetime.now(timezone.utc)
        decision_id = str(uuid4())
        out = AdvisorDecisionV1(
            decision_id=decision_id,
            user_id=str(user.id),
            preview_id=preview_id,
            proposal_id=proposal_id,
            validation_id=validation_id,
            status="accepted",
            accepted_at=now,
            profile_hash_at_accept=validation.current_profile_hash,
            engine_version_at_accept=validation.current_engine_version,
            apply_ready=True,
        )

        entity = AdvisorDecision(
            decision_id=decision_id,
            user_id=user.id,
            preview_id=preview_id,
            proposal_id=proposal_id,
            validation_id=validation_id,
            status="accepted",
            accepted_at=now,
            profile_hash_at_accept=out.profile_hash_at_accept,
            engine_version_at_accept=out.engine_version_at_accept,
            apply_ready=True,
            decision_payload=out.model_dump(mode="json"),
        )
        await self.decisions.create(db, entity)
        return out
