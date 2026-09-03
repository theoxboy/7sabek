from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GuestIdempotencyKey(Base):
    """
    One row per `Idempotency-Key` seen on `POST /auth/guest`.

    A retried create (flaky mobile network, double tap) replays the stored
    guest instead of inserting a second one. Rows are short-lived (24h) and
    swept opportunistically.
    """

    __tablename__ = "guest_idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    guest_token: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
