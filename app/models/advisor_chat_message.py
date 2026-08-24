from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdvisorChatMessage(Base):
    """One turn of the AI advisor conversation, owned by the user.

    The conversation used to live in the browser's localStorage, which made it
    a per-device artefact: the same account saw a different history on the web
    and on the phone, and clearing the browser lost it. Storing it here is what
    lets every client of the account continue the same conversation.
    """

    __tablename__ = "advisor_chat_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Insertion order, and the only reliable one: Postgres now() is the
    # transaction timestamp, so the user turn and the answer written in the same
    # request share a created_at to the microsecond and cannot be told apart.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # "user" or "assistant" only: the system prompt is rebuilt server-side on
    # every call and must never be replayed from stored history.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_advisor_chat_messages_user_seq", "user_id", "seq"),
    )
