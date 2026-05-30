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
    templates_enabled: bool
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


class EmailCenterSystemStatusFlagsOut(BaseModel):
    ai_suggestions_enabled: bool
    allow_user_send: bool
    allow_bulk_send: bool
    allow_scheduling: bool
    allow_salary_reminders: bool
    templates_enabled: bool
    allow_open_tracking: bool
    allow_click_tracking: bool


class EmailCenterAISuggestIn(BaseModel):
    language: str = Field(default="fr", max_length=16)
    tone: str = Field(default="friendly", max_length=40)
    goal: str = Field(min_length=1, max_length=2000)
    audience_type: str = Field(default="test", max_length=40)
    user_id: Optional[UUID] = None
    cta_url: str = Field(default="", max_length=500)
    cta_label_hint: str = Field(default="", max_length=120)
    personalize_with_first_name: bool = True


class EmailCenterAISuggestOut(BaseModel):
    subject: str
    preview_text: str
    body: str
    cta_label: str


class EmailTemplateCreate(BaseModel):
    key: Optional[str] = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(default="custom", max_length=50)
    language: str = Field(default="fr", max_length=16)
    subject: str = Field(min_length=1, max_length=300)
    preview_text: Optional[str] = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    cta_label: Optional[str] = Field(default=None, max_length=120)
    cta_url: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True


class EmailTemplateUpdate(BaseModel):
    key: Optional[str] = Field(default=None, max_length=120)
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    category: Optional[str] = Field(default=None, max_length=50)
    language: Optional[str] = Field(default=None, max_length=16)
    subject: Optional[str] = Field(default=None, min_length=1, max_length=300)
    preview_text: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    cta_label: Optional[str] = Field(default=None, max_length=120)
    cta_url: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class EmailTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: Optional[str] = None
    name: str
    category: str
    language: str
    subject: str
    preview_text: Optional[str] = None
    body: str
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    is_active: bool
    created_by_admin_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class EmailTemplateListOut(BaseModel):
    enabled: bool = True
    items: List[EmailTemplateOut]


class EmailCenterSystemStatusMailProviderOut(BaseModel):
    provider: str
    from_email: str
    api_base_configured: bool
    token_configured: bool


class EmailCenterSystemStatusDatabaseOut(BaseModel):
    email_design_settings_table: bool
    email_deliveries_table: bool
    error: Optional[str] = None


class EmailCenterSystemStatusCapabilitiesOut(BaseModel):
    send_test: bool
    design_settings: bool
    history: bool
    user_search: bool
    user_preview: bool
    send_user: bool
    bulk_send: bool
    scheduling: bool
    salary_reminders: bool
    ai_suggestions: bool
    templates: bool


class EmailCenterSystemStatusAIOut(BaseModel):
    ai_suggestions_enabled: bool
    ai_gateway_configured: bool
    ai_default_model_configured: bool
    ai_capability: str


class EmailCenterSystemStatusTemplatesOut(BaseModel):
    templates_enabled: bool
    templates_count: int
    active_templates_count: int
    templates_capability: str


class EmailCenterSystemStatusSafetyOut(BaseModel):
    bulk_send_blocked: bool
    scheduling_blocked: bool
    salary_reminders_blocked: bool
    test_recipient_configured: bool
    production_send_enabled: bool


class EmailCenterSystemStatusStatsOut(BaseModel):
    total_deliveries: int
    pending: int
    sent: int
    failed: int
    skipped: int
    latest_delivery_at: Optional[datetime] = None


class EmailCenterSystemStatusOut(BaseModel):
    enabled: bool
    mode: str
    kill_switch: bool
    flags: EmailCenterSystemStatusFlagsOut
    mail_provider: EmailCenterSystemStatusMailProviderOut
    ai: EmailCenterSystemStatusAIOut
    templates: EmailCenterSystemStatusTemplatesOut
    database: EmailCenterSystemStatusDatabaseOut
    capabilities: EmailCenterSystemStatusCapabilitiesOut
    safety: EmailCenterSystemStatusSafetyOut
    stats: EmailCenterSystemStatusStatsOut
