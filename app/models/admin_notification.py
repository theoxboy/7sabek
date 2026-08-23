from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AdminNotification(Base):
    __tablename__ = "admin_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    title_fr: Mapped[str] = mapped_column(String(255), nullable=False)
    title_ar: Mapped[str] = mapped_column(String(255), nullable=False)
    message_fr: Mapped[str] = mapped_column(Text, nullable=False)
    message_ar: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), default="general", nullable=False)
    target_audience: Mapped[str] = mapped_column(String(50), default="all", nullable=False)
    target_user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), default="none", nullable=False)
    action_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    haptic_effect: Mapped[str] = mapped_column(String(50), default="Success", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    read_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AdminNotificationRead(Base):
    __tablename__ = "admin_notification_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    notification_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token: Mapped[str] = mapped_column(String(500), unique=True, index=True, nullable=False)
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), default="android", nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="fr", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
