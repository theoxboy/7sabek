from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_pre_apply_validation import AdvisorPreApplyValidation
from app.models.user import User
from app.repositories.advisor.preview_repository import AdvisorPreviewRepository
from app.repositories.advisor.validation_repository import AdvisorValidationRepository
from app.schemas.advisor.contracts import AdvisorPreApplyValidationResultV1


class ValidatePreApplyService:
    """Validates freshness and apply eligibility for a selected proposal."""

    def __init__(
        self,
        previews: AdvisorPreviewRepository,
        validations: AdvisorValidationRepository,
    ) -> None:
        self.previews = previews
        self.validations = validations

    async def validate(
        self,
        db: AsyncSession,
        user: User,
        preview_id: str,
        proposal_id: str,
    ) -> AdvisorPreApplyValidationResultV1:
        # TODO(advisor-v1): full freshness checks and proposal existence checks.
        preview = await self.previews.get_by_preview_id(db, preview_id)
        if preview is None:
            return AdvisorPreApplyValidationResultV1(
                ok=False,
                can_apply=False,
                validation_id=None,
                reasons=["ADVISOR_PREVIEW_NOT_FOUND"],
                freshness={
                    "is_stale": True,
                    "current_profile_hash": "",
                    "preview_profile_hash": "",
                    "current_engine_version": "",
                    "preview_engine_version": "",
                },
                gating_snapshot={
                    "degraded_mode": True,
                    "can_recommend_confidently": False,
                },
            )

        validation_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=15)

        out = AdvisorPreApplyValidationResultV1(
            ok=True,
            can_apply=True,
            validation_id=validation_id,
            reasons=[],
            freshness={
                "is_stale": False,
                "current_profile_hash": "TODO_PROFILE_HASH",
                "preview_profile_hash": preview.profile_hash,
                "current_engine_version": preview.engine_version,
                "preview_engine_version": preview.engine_version,
            },
            gating_snapshot={
                "degraded_mode": preview.degraded_mode,
                "can_recommend_confidently": preview.can_recommend_confidently,
            },
        )

        entity = AdvisorPreApplyValidation(
            validation_id=validation_id,
            user_id=user.id,
            preview_id=preview_id,
            proposal_id=proposal_id,
            status="valid",
            expires_at=expires_at,
            result_ok=out.ok,
            can_apply=out.can_apply,
            reasons=out.reasons,
            current_profile_hash=out.freshness.current_profile_hash,
            preview_profile_hash=out.freshness.preview_profile_hash,
            current_engine_version=out.freshness.current_engine_version,
            preview_engine_version=out.freshness.preview_engine_version,
            validation_payload=out.model_dump(mode="json"),
        )
        await self.validations.create(db, entity)
        return out
