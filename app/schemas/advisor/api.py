from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.advisor.contracts import (
    AdvisorDecisionV1,
    AdvisorPreApplyValidationResultV1,
    AdvisorPreviewResponseV1,
)


class AdvisorApiBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdvisorApiError(AdvisorApiBaseModel):
    code: str
    message: str
    details: Optional[dict[str, Any]] = None


class PreviewFreshnessOut(AdvisorApiBaseModel):
    profile_hash: str
    engine_version: str
    generated_at: datetime
    expires_at: datetime


class PostAdvisorPreviewRequest(AdvisorApiBaseModel):
    user_id: UUID
    source: str = Field(pattern="^(onboarding|manual)$")
    force_regenerate: bool = False


class PostAdvisorPreviewResponse(AdvisorApiBaseModel):
    preview_id: UUID
    advisor_preview: AdvisorPreviewResponseV1
    freshness: PreviewFreshnessOut


class PostAdvisorValidatePreApplyRequest(AdvisorApiBaseModel):
    user_id: UUID
    preview_id: UUID
    proposal_id: str


class PostAdvisorAcceptRequest(AdvisorApiBaseModel):
    user_id: UUID
    preview_id: UUID
    proposal_id: str
    validation_id: UUID
    confirm: bool


# Backward-compatible aliases used by current service/router wiring.
AdvisorPreviewRequestIn = PostAdvisorPreviewRequest
AdvisorPreviewEnvelopeOut = PostAdvisorPreviewResponse
AdvisorValidatePreApplyRequestIn = PostAdvisorValidatePreApplyRequest
AdvisorAcceptRequestIn = PostAdvisorAcceptRequest
AdvisorErrorOut = AdvisorApiError
AdvisorValidatePreApplyOut = AdvisorPreApplyValidationResultV1
AdvisorAcceptOut = AdvisorDecisionV1
