from __future__ import annotations

import re

from app.models import Envelope

_MULTISPACE_RE = re.compile(r"\s+")
_RESERVED_ENVELOPE_KEYS = {"cash", "epargnes"}
_DEBT_KEYWORDS = ("dettes", "debt", "debts", "credit", "repayment", "loan", "دين")


def normalize_name(value: str) -> str:
    return _MULTISPACE_RE.sub(" ", value).strip()


def name_key(value: str) -> str:
    return normalize_name(value).casefold()


def is_reserved_envelope_name(value: str) -> bool:
    return name_key(value) in _RESERVED_ENVELOPE_KEYS


def is_category_mappable_envelope(envelope: Envelope) -> bool:
    return not envelope.is_cash and not envelope.is_default_savings and not envelope.is_goal


def is_sweep_eligible_envelope(envelope: Envelope) -> bool:
    return (
        not envelope.is_cash
        and not envelope.is_default_savings
        and not envelope.is_goal
        and not envelope.rollover_enabled
    )


def is_rollover_off_forbidden_envelope(envelope: Envelope) -> bool:
    key = name_key(envelope.name)
    is_debt = any(keyword in key for keyword in _DEBT_KEYWORDS)
    return envelope.is_goal or is_debt
