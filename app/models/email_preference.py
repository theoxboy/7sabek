from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailPreference(Base):
    __tablename__ = "email_preferences"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    salary_reminders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    tips_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    product_updates_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    marketing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    security_emails_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
