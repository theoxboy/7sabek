from __future__ import annotations

from datetime import datetime

from typing import Optional

from pydantic import BaseModel, ConfigDict


class AdminActivityLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor_email: Optional[str] = None
    actor_ip: Optional[str] = None
    event_type: str
    status: str
    message: str
