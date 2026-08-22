from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ContactMessageCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    contact_info: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=10)


class ContactMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    full_name: str
    contact_info: str
    subject: str
    message: str
    ticket_ref: str
    matched_user_id: Optional[UUID] = None

