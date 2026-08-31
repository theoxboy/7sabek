from __future__ import annotations

import re

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Envelope

_MULTISPACE_RE = re.compile(r"\s+")
_RESERVED_ENVELOPE_KEYS = {"cash", "epargnes"}

# Only a *heuristic*: used to seed the explicit `envelopes.is_debt` column at
# creation and to backfill it once. The column, not this list, is what the
# sweep and the rollover guard actually read — a name that slips past these
# keywords no longer strands the fund, it is just a data value to correct.
_DEBT_KEYWORDS = (
    "dette",
    "dettes",
    "debt",
    "debts",
    "credit",
    "crédit",
    "crédits",
    "kredit",
    "كريدي",
    "repayment",
    "repayments",
    "loan",
    "loans",
    "salaf",
    "سلف",
    "دين",
    "الديون",
    "ديون",
    "قرض",
    "قروض",
)


def name_looks_like_debt(name: str | None) -> bool:
    """Heuristic debt-name match. Seeds `is_debt` at creation; never the sole gate."""
    if not name:
        return False
    key = name_key(name)
    return any(keyword in key for keyword in _DEBT_KEYWORDS)



def normalize_name(value: str) -> str:
    cleaned = _MULTISPACE_RE.sub(" ", value).strip().lower()
    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ة": "ه",
        "ى": "ي",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    return cleaned


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
        and not bool(getattr(envelope, "is_debt", False))
        and not envelope.rollover_enabled
    )


def is_rollover_off_forbidden_envelope(envelope: Envelope) -> bool:
    # Reads the explicit is_debt flag now, not a live name match — so a debt
    # envelope whose name doesn't hit the keywords is still protected once the
    # flag is set, and a false-positive name can be corrected by clearing it.
    return envelope.is_goal or bool(getattr(envelope, "is_debt", False))
