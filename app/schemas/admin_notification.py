from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class AdminNotificationCreate(BaseModel):
    title_fr: str = Field(min_length=1, max_length=255)
    title_ar: str = Field(min_length=1, max_length=255)
    message_fr: str = Field(min_length=1)
    message_ar: str = Field(min_length=1)
    notification_type: str = Field(default="general", max_length=50)
    target_audience: str = Field(default="all", max_length=50)
    target_user_email: Optional[str] = Field(default=None, max_length=255)
    action_type: str = Field(default="none", max_length=50)
    action_url: Optional[str] = Field(default=None, max_length=500)
    haptic_effect: str = Field(default="Success", max_length=50)
    priority: str = Field(default="normal", max_length=20)


class AdminNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    title_fr: str
    title_ar: str
    message_fr: str
    message_ar: str
    notification_type: str
    target_audience: str
    target_user_email: Optional[str] = None
    action_type: str
    action_url: Optional[str] = None
    haptic_effect: str
    priority: str
    is_active: bool
    sent_count: int
    read_count: int
    created_by_email: Optional[str] = None


class ClientBroadcastNotificationOut(BaseModel):
    id: int
    created_at: datetime
    title: str
    message: str
    notification_type: str
    action_type: str
    action_url: Optional[str] = None
    haptic_effect: str
    priority: str
    is_read: bool = False
