from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BackupRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    kind: str
    status: str
    mode: Optional[str] = None
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    duration_ms: Optional[int] = None
    actor_email: Optional[str] = None
    actor_ip: Optional[str] = None
    message: Optional[str] = None


class BackupStatusOut(BaseModel):
    last_scheduled: Optional[BackupRecordOut] = None
    last_snapshot: Optional[BackupRecordOut] = None
    retention_count: int
    schedule_days: int
