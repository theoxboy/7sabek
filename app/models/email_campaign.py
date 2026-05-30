from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    audience_type: Mapped[str] = mapped_column(String(40), nullable=False)
    audience_filter_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    language_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto", server_default="auto")
    template_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    subject_by_language_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    preview_by_language_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    body_by_language_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    cta_label_by_language_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    cta_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    design_settings_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    estimated_recipient_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_dry_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_admin_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    send_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    send_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_recipients: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_sent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_failed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_skipped: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_admin_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
