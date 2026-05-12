from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorDecision(Base):
    __tablename__ = "advisor_decisions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    preview_id: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    validation_id: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted", server_default="accepted")

    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    profile_hash_at_accept: Mapped[str] = mapped_column(String(128), nullable=False)
    engine_version_at_accept: Mapped[str] = mapped_column(String(64), nullable=False)
    apply_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    consumed_by_apply_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_advisor_decisions_user_status", "user_id", "status"),
        Index("ix_advisor_decisions_preview_proposal", "preview_id", "proposal_id"),
        Index("ix_advisor_decisions_consumed_by_apply_id", "consumed_by_apply_id", unique=True),
    )
