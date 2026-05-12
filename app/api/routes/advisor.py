from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.advisor import (
    AdvisorDecisionRepository,
    AdvisorPreviewRepository,
    AdvisorValidationRepository,
)
from app.schemas.advisor.api import (
    AdvisorAcceptOut,
    AdvisorAcceptRequestIn,
    AdvisorPreviewEnvelopeOut,
    AdvisorPreviewRequestIn,
    AdvisorValidatePreApplyOut,
    AdvisorValidatePreApplyRequestIn,
)
from app.services.advisor import (
    AcceptService,
    AdvisorPreviewService,
    FallbackExplainService,
    GatingService,
    NormalizerService,
    ProposalEngineService,
    ValidatePreApplyService,
)

router = APIRouter(prefix="/advisor")

_PREVIEW_ERROR_HTTP_STATUS: dict[str, int] = {
    "ADVISOR_USER_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADVISOR_SOURCE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADVISOR_NORMALIZER_FAILED": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ADVISOR_GATING_FAILED": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ADVISOR_ENGINE_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "ADVISOR_EXPLAIN_FALLBACK_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "ADVISOR_PREVIEW_PERSIST_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


@router.post("/preview", response_model=AdvisorPreviewEnvelopeOut)
async def advisor_preview(
    payload: AdvisorPreviewRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorPreviewEnvelopeOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = AdvisorPreviewService(
        normalizer=NormalizerService(),
        gating=GatingService(),
        engine=ProposalEngineService(),
        fallback_explain=FallbackExplainService(),
        previews=AdvisorPreviewRepository(),
    )

    try:
        out = await service.generate(
            db=db,
            user=current_user,
            source=payload.source,
            force_regenerate=payload.force_regenerate,
        )
    except RuntimeError as exc:
        await db.rollback()
        code = str(exc)
        raise HTTPException(
            status_code=_PREVIEW_ERROR_HTTP_STATUS.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=code,
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="INTERNAL_ERROR") from exc

    await db.commit()
    return out


@router.post("/validate-pre-apply", response_model=AdvisorValidatePreApplyOut)
async def advisor_validate_pre_apply(
    payload: AdvisorValidatePreApplyRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorValidatePreApplyOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = ValidatePreApplyService(
        previews=AdvisorPreviewRepository(),
        validations=AdvisorValidationRepository(),
    )
    out = await service.validate(
        db=db,
        user=current_user,
        preview_id=payload.preview_id,
        proposal_id=payload.proposal_id,
    )
    await db.commit()
    return out


@router.post("/accept", response_model=AdvisorAcceptOut)
async def advisor_accept(
    payload: AdvisorAcceptRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorAcceptOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = AcceptService(
        decisions=AdvisorDecisionRepository(),
        validations=AdvisorValidationRepository(),
    )
    try:
        out = await service.accept(
            db=db,
            user=current_user,
            preview_id=payload.preview_id,
            proposal_id=payload.proposal_id,
            validation_id=payload.validation_id,
            confirm=payload.confirm,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    return out
