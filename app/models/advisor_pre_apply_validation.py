from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorPreApplyValidation(Base):
    __tablename__ = "advisor_pre_apply_validations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    validation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    preview_id: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="valid", server_default="valid")

    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    result_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    can_apply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    current_profile_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    preview_profile_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    current_engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_engine_version: Mapped[str] = mapped_column(String(64), nullable=False)

    validation_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_advisor_validations_user_preview", "user_id", "preview_id"),
        Index("ix_advisor_validations_expires_at", "expires_at"),
        Index(
            "ix_advisor_validations_idempotence",
            "user_id",
            "preview_id",
            "proposal_id",
            "current_profile_hash",
            "current_engine_version",
        ),
    )
