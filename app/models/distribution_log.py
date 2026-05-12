from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DistributionLog(Base):
    __tablename__ = "distribution_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    transaction_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    income_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    cash_before: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cash_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    config_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("distribution_saved_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    config_version: Mapped[Optional[int]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied", server_default="applied")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items = relationship(
        "DistributionLogItem",
        back_populates="log",
        cascade="all, delete-orphan",
    )
