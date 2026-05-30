from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailDesignSettings(Base):
    __tablename__ = "email_design_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    brand_name: Mapped[str] = mapped_column(String(120), nullable=False, default="7sabek")
    logo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    primary_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#0f172a")
    button_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#0f172a")
    footer_text: Mapped[str] = mapped_column(
        String(500), nullable=False, default="Merci d'utiliser 7sabek."
    )
    support_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
