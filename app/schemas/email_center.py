from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
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
    recipient_preview_enabled: bool
    campaigns_enabled: bool
    campaign_test_send_enabled: bool
    preferences_enabled: bool
    suppression_enabled: bool
    delivery_queue_enabled: bool
    bulk_require_test_send: bool
    bulk_require_dry_run: bool


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
    recipient_preview: str
    campaigns: str
    campaign_test_send: str
    preferences: str
    suppression: str
    bulk_send_capability: str
    queue: str


class EmailCenterSystemStatusAIOut(BaseModel):
    ai_suggestions_enabled: bool
    ai_gateway_configured: bool
    ai_default_model_configured: bool
    ai_capability: str


class EmailCenterSystemStatusTemplatesOut(BaseModel):
    templates_enabled: bool
    templates_count: Optional[int] = None
    active_templates_count: Optional[int] = None
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
    retry: int
    suppression_count: Optional[int] = None
    active_suppression_count: Optional[int] = None
    pending_deliveries_count: int = 0
    retry_deliveries_count: int = 0
    latest_delivery_at: Optional[datetime] = None


class EmailCenterSystemStatusCampaignsOut(BaseModel):
    campaigns_enabled: bool
    campaign_drafts_count: Optional[int] = None
    campaign_capability: str


class EmailCenterSystemStatusBulkOut(BaseModel):
    bulk_send_enabled: bool
    bulk_max_recipients: int
    require_test_send: bool
    require_dry_run: bool
    confirmation_text: str


class EmailCenterSystemStatusQueueOut(BaseModel):
    delivery_queue_enabled: bool
    batch_size: int
    max_attempts: int
    retry_delay_minutes: int
    rate_limit_per_minute: int


class EmailCenterSystemStatusOut(BaseModel):
    enabled: bool
    mode: str
    kill_switch: bool
    unsubscribe_token_ttl_days: int
    flags: EmailCenterSystemStatusFlagsOut
    mail_provider: EmailCenterSystemStatusMailProviderOut
    ai: EmailCenterSystemStatusAIOut
    templates: EmailCenterSystemStatusTemplatesOut
    campaigns: EmailCenterSystemStatusCampaignsOut
    bulk: EmailCenterSystemStatusBulkOut
    queue: EmailCenterSystemStatusQueueOut
    database: EmailCenterSystemStatusDatabaseOut
    capabilities: EmailCenterSystemStatusCapabilitiesOut
    safety: EmailCenterSystemStatusSafetyOut
    stats: EmailCenterSystemStatusStatsOut


class RecipientsPreviewIn(BaseModel):
    audience_type: str = Field(max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    template_id: Optional[UUID] = None
    subject: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = Field(default=None, max_length=20000)
    cta_label: Optional[str] = Field(default=None, max_length=120)
    cta_url: Optional[str] = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=200)


class RecipientsPreviewItemOut(BaseModel):
    user_id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    detected_language: str
    eligible: bool
    reason: str
    skip_reason: Optional[str] = None


class RecipientsPreviewOut(BaseModel):
    enabled: bool
    audience_type: str
    total_matched: int
    returned_count: int
    items: List[RecipientsPreviewItemOut]
    warnings: List[str]


class RecipientsPreviewUserEmailIn(BaseModel):
    user_id: UUID
    template_id: Optional[UUID] = None
    subject: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = Field(default=None, max_length=20000)
    cta_label: Optional[str] = Field(default=None, max_length=120)
    cta_url: Optional[str] = Field(default=None, max_length=500)


class RecipientsPreviewUserEmailOut(BaseModel):
    user_id: UUID
    email: str
    detected_language: str
    subject: str
    preview_text: str
    body_html: str
    body_text: str
    cta_label: str
    cta_url: str


class CampaignSendTestIn(BaseModel):
    language: str = Field(max_length=16)
    test_email: Optional[str] = Field(default=None, max_length=255)


class CampaignSendIn(BaseModel):
    confirmation: str = Field(min_length=1, max_length=40)


class EmailCampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: str = Field(default="manual", max_length=40)
    audience_type: str = Field(max_length=40)
    audience_filter_json: Optional[Dict[str, Any]] = None
    language_mode: str = Field(default="auto", max_length=16)
    template_id: Optional[UUID] = None
    subject_by_language_json: Optional[Dict[str, Any]] = None
    preview_by_language_json: Optional[Dict[str, Any]] = None
    body_by_language_json: Optional[Dict[str, Any]] = None
    cta_label_by_language_json: Optional[Dict[str, Any]] = None
    cta_url: Optional[str] = Field(default=None, max_length=500)
    design_settings_json: Optional[Dict[str, Any]] = None
    status: Optional[str] = Field(default="draft", max_length=20)


class EmailCampaignUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[str] = Field(default=None, max_length=40)
    audience_type: Optional[str] = Field(default=None, max_length=40)
    audience_filter_json: Optional[Dict[str, Any]] = None
    language_mode: Optional[str] = Field(default=None, max_length=16)
    template_id: Optional[UUID] = None
    subject_by_language_json: Optional[Dict[str, Any]] = None
    preview_by_language_json: Optional[Dict[str, Any]] = None
    body_by_language_json: Optional[Dict[str, Any]] = None
    cta_label_by_language_json: Optional[Dict[str, Any]] = None
    cta_url: Optional[str] = Field(default=None, max_length=500)
    design_settings_json: Optional[Dict[str, Any]] = None
    estimated_recipient_count: Optional[int] = None
    status: Optional[str] = Field(default=None, max_length=20)


class EmailCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    type: str
    status: str
    audience_type: str
    audience_filter_json: Optional[Dict[str, Any]] = None
    language_mode: str
    template_id: Optional[UUID] = None
    subject_by_language_json: Optional[Dict[str, Any]] = None
    preview_by_language_json: Optional[Dict[str, Any]] = None
    body_by_language_json: Optional[Dict[str, Any]] = None
    cta_label_by_language_json: Optional[Dict[str, Any]] = None
    cta_url: Optional[str] = None
    design_settings_json: Optional[Dict[str, Any]] = None
    estimated_recipient_count: Optional[int] = None
    last_dry_run_at: Optional[datetime] = None
    last_test_sent_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_admin_id: Optional[UUID] = None
    sent_at: Optional[datetime] = None
    send_started_at: Optional[datetime] = None
    send_finished_at: Optional[datetime] = None
    total_recipients: Optional[int] = None
    total_sent: Optional[int] = None
    total_failed: Optional[int] = None
    total_skipped: Optional[int] = None
    created_by_admin_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class EmailCampaignListOut(BaseModel):
    enabled: bool
    capability: str
    items: List[EmailCampaignOut]
    limit: int
    offset: int


class EmailPreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    salary_reminders_enabled: bool
    tips_enabled: bool
    product_updates_enabled: bool
    marketing_enabled: bool
    security_emails_enabled: bool
    created_at: datetime
    updated_at: datetime


class EmailPreferencePublicOut(BaseModel):
    email: str
    salary_reminders_enabled: bool
    tips_enabled: bool
    product_updates_enabled: bool
    marketing_enabled: bool
    security_emails_enabled: bool


class EmailPreferenceUpdate(BaseModel):
    salary_reminders_enabled: Optional[bool] = None
    tips_enabled: Optional[bool] = None
    product_updates_enabled: Optional[bool] = None
    marketing_enabled: Optional[bool] = None
    security_emails_enabled: Optional[bool] = None


class EmailUnsubscribeRequest(BaseModel):
    token: str = Field(min_length=10, max_length=1000)


class EmailSuppressionCreate(BaseModel):
    email: EmailStr
    user_id: Optional[UUID] = None
    category: Optional[str] = Field(default=None, max_length=50)
    reason: str = Field(min_length=1, max_length=50)
    source: Optional[str] = Field(default=None, max_length=50)


class EmailSuppressionUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=50)
    reason: Optional[str] = Field(default=None, max_length=50)
    source: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = None


class EmailSuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    user_id: Optional[UUID] = None
    category: Optional[str] = None
    reason: str
    source: Optional[str] = None
    is_active: bool
    created_by_admin_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    deactivated_at: Optional[datetime] = None


class EmailSuppressionListOut(BaseModel):
    items: List[EmailSuppressionOut]
    limit: int
    offset: int
    total: int


class DeliveryQueueProcessIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)


class DeliveryQueueProcessOut(BaseModel):
    attempted: int
    sent: int
    failed: int
    retry: int
    remaining_pending: int


class DeliveryQueueStatusOut(BaseModel):
    pending_count: int
    retry_count: int
    failed_count: int
    sent_today: int
    next_due_count: int
    batch_size: int
    max_attempts: int
    retry_delay_minutes: int
    rate_limit_per_minute: int
