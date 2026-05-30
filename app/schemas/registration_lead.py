from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegistrationLeadUpsertIn(BaseModel):
    lead_id: Optional[UUID] = None
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    birth_date: Optional[date] = None
    email: Optional[str] = Field(default=None, max_length=255)
    country: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    language: Optional[str] = Field(default=None, max_length=16)
    current_step: Optional[int] = Field(default=None, ge=1, le=99)
    event: Optional[str] = Field(default=None, max_length=40)


class RegistrationLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: UUID
    status: str


class RegistrationLeadListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    language: Optional[str] = None
    current_step: Optional[int] = None
    highest_step_reached: Optional[int] = None
    status: str
    converted_user_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime


class RegistrationLeadListOut(BaseModel):
    items: List[RegistrationLeadListItemOut]
    total: int
    limit: int
    offset: int


class RegistrationLeadDismissIn(BaseModel):
    status: str


class RegistrationLeadStatsOut(BaseModel):
    total: int
    email_captured: int
    partial_no_email: int
    converted: int
    dismissed: int
    last_24h: int


class RegistrationLeadRecipientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    detected_language: str
    reason: str = "incomplete_registration"
    metadata_json: Optional[Dict[str, Any]] = None
