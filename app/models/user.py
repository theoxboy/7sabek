from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    sweep_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    next_sweep_date: Mapped[date] = mapped_column(Date, nullable=False)
    auto_distribution_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    auto_sweep_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    profile_photo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    leaderboard_name: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="user", server_default="user"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    must_reset_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_beta_tester: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    force_onboarding_v2_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    force_tour_replay_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    suspended_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    password_reset_block_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default="none"
    )
    password_reset_blocked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    password_reset_block_reason: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    password_reset_blocked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    password_reset_blocked_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    envelopes: Mapped[List["Envelope"]] = relationship(back_populates="user")
    categories: Mapped[List["Category"]] = relationship(back_populates="user")
    category_envelope_maps: Mapped[List["CategoryEnvelopeMap"]] = relationship(
        back_populates="user"
    )
    envelope_periods: Mapped[List["EnvelopePeriod"]] = relationship(
        back_populates="user"
    )
    envelope_allocations: Mapped[List["EnvelopeAllocation"]] = relationship(
        back_populates="user"
    )
    transactions: Mapped[List["Transaction"]] = relationship(back_populates="user")
    envelope_movements: Mapped[List["EnvelopeMovement"]] = relationship(
        back_populates="user"
    )
    sweeps: Mapped[List["Sweep"]] = relationship(back_populates="user")
    page_views: Mapped[List["PageView"]] = relationship(back_populates="user")
    onboarding_v2_records: Mapped[List["OnboardingV2Record"]] = relationship(
        back_populates="user"
    )
    shiftpilot_state: Mapped[Optional["UserShiftPilotState"]] = relationship(
        back_populates="user",
        uselist=False,
    )
