from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorPreview(Base):
    __tablename__ = "advisor_previews"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )
    preview_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")

    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="AdvisorPreviewResponseV1"
    )
    profile_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    gating_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    degraded_mode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    can_recommend_confidently: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recommended_proposal_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    warnings_snapshot: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    blocking_issues_snapshot: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    data_quality_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    preview_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    superseded_by_preview_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_advisor_previews_user_id", "user_id"),
        Index("ix_advisor_previews_status", "status"),
        Index("ix_advisor_previews_expires_at", "expires_at"),
        Index("ix_advisor_previews_user_generated", "user_id", "generated_at"),
        Index("ix_advisor_previews_user_status", "user_id", "status"),
    )
