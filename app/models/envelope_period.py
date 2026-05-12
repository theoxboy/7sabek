from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EnvelopePeriod(Base):
    __tablename__ = "envelope_periods"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "envelope_id",
            "period_start",
            name="uq_env_period_user_env_start",
        ),
        CheckConstraint("period_end > period_start", name="ck_env_period_date_range"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    envelope_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("envelopes.id"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    rollover_from_period_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("envelope_periods.id"),
        nullable=True,
    )
    swept_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="envelope_periods")
    envelope: Mapped["Envelope"] = relationship(back_populates="periods")
    allocations: Mapped[List["EnvelopeAllocation"]] = relationship(
        back_populates="envelope_period"
    )
    movements: Mapped[List["EnvelopeMovement"]] = relationship(
        back_populates="envelope_period"
    )
    rollover_from_period: Mapped[Optional["EnvelopePeriod"]] = relationship(
        back_populates="rollover_children",
        remote_side=[id],
    )
    rollover_children: Mapped[List["EnvelopePeriod"]] = relationship(
        back_populates="rollover_from_period",
    )
    sweeps_out: Mapped[List["Sweep"]] = relationship(
        back_populates="from_envelope_period",
        foreign_keys="Sweep.from_envelope_period_id",
    )
    sweeps_in: Mapped[List["Sweep"]] = relationship(
        back_populates="to_envelope_period",
        foreign_keys="Sweep.to_envelope_period_id",
    )
