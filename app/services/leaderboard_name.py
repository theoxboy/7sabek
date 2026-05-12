from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import HTTPException

from app.models import User


_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9 _.-]+$")

_BANNED_TERMS: set[str] = {
    "connard",
    "conne",
    "salope",
    "salaud",
    "pute",
    "encule",
    "enculer",
    "fdp",
    "fuck",
    "fucking",
    "bitch",
    "asshole",
    "nigger",
    "nigga",
    "cunt",
    "motherfucker",
    "puta",
}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_for_filter(value: str) -> str:
    value = _strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]", "", value)


def _contains_banned_term(value: str, banned: Optional[Iterable[str]] = None) -> bool:
    normalized = _normalize_for_filter(value)
    terms = banned or _BANNED_TERMS
    return any(term in normalized for term in terms)


def validate_leaderboard_name(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) < 3 or len(cleaned) > 20:
        raise HTTPException(
            status_code=400, detail="PSEUDO_LENGTH_INVALID"
        )
    if not _ALLOWED_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=400, detail="PSEUDO_CHARS_INVALID"
        )
    return cleaned


def is_leaderboard_name_banned(value: str) -> bool:
    return _contains_banned_term(value)


def apply_leaderboard_name_or_suspend(
    user: User, raw_value: str
) -> tuple[str, bool]:
    cleaned = validate_leaderboard_name(raw_value)
    if _contains_banned_term(cleaned):
        user.status = "suspended"
        user.suspended_until = datetime.now(timezone.utc) + timedelta(days=10)
        return cleaned, True
    return cleaned, False
