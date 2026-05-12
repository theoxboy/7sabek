from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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


class Sweep(Base):
    __tablename__ = "sweeps"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_sweeps_amount_positive"),
        CheckConstraint(
            "from_envelope_period_id <> to_envelope_period_id",
            name="ck_sweeps_periods_distinct",
        ),
        UniqueConstraint(
            "user_id",
            "from_envelope_period_id",
            "to_envelope_period_id",
            "amount",
            "swept_on",
            name="uq_sweeps_user_from_to_amount_swept_on",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    from_envelope_period_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("envelope_periods.id"),
        nullable=False,
    )
    to_envelope_period_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("envelope_periods.id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    swept_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="sweeps")
    from_envelope_period: Mapped["EnvelopePeriod"] = relationship(
        back_populates="sweeps_out",
        foreign_keys=[from_envelope_period_id],
    )
    to_envelope_period: Mapped["EnvelopePeriod"] = relationship(
        back_populates="sweeps_in",
        foreign_keys=[to_envelope_period_id],
    )
