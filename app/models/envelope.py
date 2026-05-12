from __future__ import annotations

from datetime import datetime
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Envelope(Base):
    __tablename__ = "envelopes"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_envelopes_user_name"),
        Index(
            "uq_envelopes_user_default_savings",
            "user_id",
            unique=True,
            postgresql_where=text("is_default_savings = true"),
        ),
        Index(
            "uq_envelopes_user_cash",
            "user_id",
            unique=True,
            postgresql_where=text("is_cash = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rollover_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_default_savings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_cash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_goal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deletable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="envelopes")
    periods: Mapped[List["EnvelopePeriod"]] = relationship(back_populates="envelope")
    category_mappings: Mapped[List["CategoryEnvelopeMap"]] = relationship(
        back_populates="envelope"
    )
