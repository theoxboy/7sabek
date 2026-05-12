from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CategoryEnvelopeMap(Base):
    __tablename__ = "category_envelope_map"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_cat_env_map_user_category"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    envelope_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("envelopes.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="category_envelope_maps")
    category: Mapped["Category"] = relationship(back_populates="envelope_mapping")
    envelope: Mapped["Envelope"] = relationship(back_populates="category_mappings")
