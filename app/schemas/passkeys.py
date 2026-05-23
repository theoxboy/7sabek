from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PasskeyRegisterOptionsOut(BaseModel):
    challenge_id: str
    public_key: dict[str, Any]


class PasskeyRegisterVerifyIn(BaseModel):
    challenge_id: str = Field(min_length=16, max_length=512)
    challenge: str = Field(min_length=16, max_length=1024)
    credential: dict[str, Any]


class PasskeyLoginOptionsIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)


class PasskeyLoginOptionsOut(BaseModel):
    challenge_id: str
    public_key: dict[str, Any]


class PasskeyLoginVerifyIn(BaseModel):
    challenge_id: str = Field(min_length=16, max_length=512)
    challenge: str = Field(min_length=16, max_length=1024)
    credential: dict[str, Any]


class PasskeyOut(BaseModel):
    id: UUID
    name: Optional[str] = None
    credential_id: str
    aaguid: Optional[str] = None
    transports: Optional[list[str]] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class PasskeyVerifyPendingOut(BaseModel):
    status: str
    message: str
