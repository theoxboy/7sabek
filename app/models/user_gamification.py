from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserGamification(Base):
    __tablename__ = "user_gamification"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    points_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points_weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points_monthly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    current_streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    longest_streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_activity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    freeze_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    freeze_week_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    week_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    month_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    freeze_pending_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    freeze_pending_streak: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    leaderboard_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User")
