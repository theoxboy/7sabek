from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


GuestAdminStatus = Literal["active", "claimed", "at_risk", "stale"]


class GuestAdminRow(BaseModel):
    id: str
    guest_created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    protection_level: Optional[int] = None
    recovery_code_ack: bool = False
    envelope_count: int = 0
    transaction_count: int = 0
    expense_total: str = "0.00"
    status: GuestAdminStatus = "active"
    claimed_at: Optional[datetime] = None
    claim_method: Optional[Literal["passkey", "email", "merge"]] = None
    device: Optional[str] = None
    country: Optional[str] = None


class GuestAdminListOut(BaseModel):
    rows: list[GuestAdminRow]
    total: int
    next_cursor: Optional[str] = None


class GuestAdminEvent(BaseModel):
    name: str
    at: datetime
    meta: Optional[dict[str, Any]] = None


class GuestAdminEnvelope(BaseModel):
    id: str
    name: str
    allocated: str = "0.00"
    spent: str = "0.00"


class GuestAdminTransaction(BaseModel):
    id: str
    kind: str
    amount: str
    label: str
    occurred_on: str


class GuestAdminAnchor(BaseModel):
    ip_prefix: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    signal_count: Optional[int] = None


class GuestAdminDetailOut(GuestAdminRow):
    events: list[GuestAdminEvent] = []
    envelopes: list[GuestAdminEnvelope] = []
    recent_transactions: list[GuestAdminTransaction] = []
    anchor: Optional[GuestAdminAnchor] = None
