from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PasskeyRegisterOptionsOut(BaseModel):
    challenge_id: str
    options: dict[str, Any]


class PasskeyRegisterVerifyIn(BaseModel):
    challenge_id: Optional[str] = Field(default=None, min_length=16, max_length=512)
    challenge: str = Field(min_length=16, max_length=1024)
    credential: dict[str, Any]
    name: Optional[str] = Field(default=None, max_length=120)


class PasskeyRegisterVerifyOut(BaseModel):
    status: str
    passkey_id: UUID
    name: Optional[str] = None


class PasskeyLoginOptionsIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)


class PasskeyLoginOptionsOut(BaseModel):
    challenge_id: str
    options: dict[str, Any]


class PasskeyLoginVerifyIn(BaseModel):
    challenge_id: Optional[str] = Field(default=None, min_length=16, max_length=512)
    challenge: str = Field(min_length=16, max_length=1024)
    credential: dict[str, Any]
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_accuracy_m: Optional[float] = Field(default=None, ge=0)
    geo_label: Optional[str] = Field(default=None, max_length=255)
    browser: Optional[str] = Field(default=None, max_length=120)
    os: Optional[str] = Field(default=None, max_length=120)
    device: Optional[str] = Field(default=None, max_length=80)


class PasskeyOut(BaseModel):
    id: UUID
    name: Optional[str] = None
    credential_id_masked: str
    aaguid: Optional[str] = None
    transports: Optional[list[str]] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None


class PasskeyDeleteOut(BaseModel):
    status: str
    message: Optional[str] = None


class PasskeyStatusOut(BaseModel):
    enabled: bool
    reason: Literal["enabled", "disabled", "not_allowed"]
