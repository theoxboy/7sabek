from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OnboardingV2Record(Base):
    __tablename__ = "onboarding_v2_records"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flow_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v2", server_default="v2"
    )
    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed", server_default="completed"
    )
    income_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    primary_objective: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    household_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="onboarding_v2_records")
