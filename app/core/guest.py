"""
Guest ("Mode Découverte") primitives.

A guest is a real ``users`` row with ``is_guest = true`` and no email/password.
Identity rests on an opaque ``guest_token`` the backend mints; the client mirrors
it into several vaults and hands it back to ``/auth/guest/resume`` when its
session cookie is lost. Only the SHA-256 of the token is stored.
"""

from __future__ import annotations

import hashlib
import secrets

# Quotas — mirrored on the client (src/lib/guestQuota.ts). Backend is the authority.
GUEST_MAX_ENVELOPES = 20
# Messages a guest may send to the AI advisor per calendar day (UTC).
GUEST_ADVISOR_MESSAGES_PER_DAY = 3

# Recovery code alphabet: base32 minus visually ambiguous chars (0/O, 1/I/L).
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_RECOVERY_LEN = 8

IDEMPOTENCY_TTL_HOURS = 24


def generate_guest_token() -> str:
    """A fresh opaque L1 secret (~256 bits, URL-safe)."""
    return secrets.token_urlsafe(32)


def generate_recovery_code() -> str:
    """An 8-char, unambiguous recovery code, e.g. ``K7M29XQP`` (shown grouped as ``K7M2-9XQP``)."""
    return "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LEN))


def normalize_recovery_code(raw: str) -> str:
    """Uppercase, strip spaces/dashes — accept what the user typed however they typed it."""
    return "".join(ch for ch in (raw or "").upper() if ch in _RECOVERY_ALPHABET)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def protection_level(user) -> int | None:
    """
    The protection-gauge figure.

    - 100 — account claimed (not a guest anymore)
    - 70  — guest who has acknowledged their recovery code
    - 40  — guest, anchor only
    - None — a normal member (gauge not shown)
    """
    if not getattr(user, "is_guest", False):
        return 100 if getattr(user, "claimed_at", None) else None
    if getattr(user, "recovery_code_ack_at", None):
        return 70
    return 40
