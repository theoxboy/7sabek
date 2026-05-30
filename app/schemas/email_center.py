from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmailCenterStatusOut(BaseModel):
    enabled: bool
    mode: str
    kill_switch: bool
    provider: str
    mail_from: str
    test_recipient_email: str
    allow_bulk_send: bool
    allow_user_send: bool
    allow_scheduling: bool
    allow_salary_reminders: bool
    allow_ai_suggestions: bool
    allow_open_tracking: bool
    allow_click_tracking: bool


class EmailDesignSettingsIn(BaseModel):
    brand_name: str = Field(min_length=1, max_length=120)
    logo_url: str = Field(default="", max_length=500)
    primary_color: str = Field(default="#0f172a", max_length=20)
    button_color: str = Field(default="#0f172a", max_length=20)
    footer_text: str = Field(default="", max_length=500)
    support_email: str = Field(default="", max_length=200)


class EmailDesignSettingsPatch(BaseModel):
    brand_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    primary_color: Optional[str] = Field(default=None, max_length=20)
    button_color: Optional[str] = Field(default=None, max_length=20)
    footer_text: Optional[str] = Field(default=None, max_length=500)
    support_email: Optional[str] = Field(default=None, max_length=200)


class EmailDesignSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    brand_name: str
    logo_url: str
    primary_color: str
    button_color: str
    footer_text: str
    support_email: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class SendTestEmailIn(BaseModel):
    to: EmailStr
    language: str = Field(default="fr", max_length=16)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    cta_label: str = Field(default="", max_length=120)
    cta_url: str = Field(default="", max_length=500)


class EmailDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    original_recipient_email: Optional[str] = None
    recipient_user_id: Optional[UUID] = None
    subject: str
    language: str
    body_html: str
    body_text: str
    status: str
    provider: str
    provider_message_id: Optional[str] = None
    note: Optional[str] = None
    error_message: Optional[str] = None
    created_by_admin_id: Optional[UUID] = None
    sent_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class EmailDeliveryHistoryOut(BaseModel):
    items: List[EmailDeliveryOut]
    page: int
    page_size: int
    total: int


class EmailCenterUserSearchOut(BaseModel):
    id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    detected_language: str


class EmailCenterUserSearchListOut(BaseModel):
    items: List[EmailCenterUserSearchOut]


class EmailCenterUserPreviewIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    cta_label: str = Field(default="", max_length=120)
    cta_url: str = Field(default="", max_length=500)


class EmailCenterUserPreviewOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    detected_language: str
    subject: str
    body_html: str
    body_text: str


class SendUserEmailIn(BaseModel):
    user_id: UUID
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    cta_label: str = Field(default="", max_length=120)
    cta_url: str = Field(default="", max_length=500)
