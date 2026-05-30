from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RegistrationLead(Base):
    __tablename__ = "registration_leads"
    __table_args__ = (
        Index("ix_registration_leads_normalized_email", "normalized_email"),
        Index("ix_registration_leads_status", "status"),
        Index("ix_registration_leads_created_at", "created_at"),
        Index("ix_registration_leads_last_seen_at", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    normalized_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    current_step: Mapped[Optional[int]] = mapped_column(nullable=True)
    highest_step_reached: Mapped[Optional[int]] = mapped_column(nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, default="register", server_default="register")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="partial", server_default="partial")
    converted_user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
