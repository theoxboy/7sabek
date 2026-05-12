from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ShiftPilotStateUpsertIn(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class ShiftPilotStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
