from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, List, Optional


@dataclass
class BackupLogEntry:
    id: int
    created_at: datetime
    actor_email: Optional[str]
    actor_ip: Optional[str]
    event_type: str
    status: str
    message: str


_lock = Lock()
_backup_in_progress: bool = False
_backup_started_at: Optional[datetime] = None
_logs: Deque[BackupLogEntry] = deque(maxlen=200)
_next_id = -1


def _next_log_id() -> int:
    global _next_id
    _next_id -= 1
    return _next_id


def set_backup_in_progress(value: bool) -> None:
    global _backup_in_progress, _backup_started_at
    with _lock:
        _backup_in_progress = value
        if value:
            _backup_started_at = datetime.now(timezone.utc)
        else:
            _backup_started_at = None


def is_backup_in_progress() -> bool:
    with _lock:
        return _backup_in_progress


def get_backup_started_at() -> Optional[datetime]:
    with _lock:
        return _backup_started_at


def add_backup_log(
    event_type: str,
    message: str,
    status: str = "info",
    actor_email: Optional[str] = None,
    actor_ip: Optional[str] = None,
) -> BackupLogEntry:
    entry = BackupLogEntry(
        id=_next_log_id(),
        created_at=datetime.now(timezone.utc),
        actor_email=actor_email,
        actor_ip=actor_ip,
        event_type=event_type,
        status=status,
        message=message,
    )
    with _lock:
        _logs.appendleft(entry)
    return entry


def list_backup_logs(limit: int = 50) -> List[BackupLogEntry]:
    with _lock:
        return list(_logs)[:limit]
