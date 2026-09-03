"""
L2 — the device *hint*, never an authority.

We hash a handful of **stable** signals (no canvas, no WebGL, no font probing —
those are exactly the ones that drift and that browsers randomise). The hash can
only ever say "a guest budget might live on this device"; it never restores data
and never identifies a user. Restoring always needs the recovery code.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Order matters — the client sends the same keys.
_STABLE_KEYS = ("platform", "language", "timezone", "screen", "cores", "memory")


def normalise_signals(raw: Any) -> dict[str, str]:
    """Keep only the stable keys, coerce to short strings, drop the rest."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in _STABLE_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        out[key] = str(value)[:64]
    return out


def anchor_hash(signals: dict[str, str], *, ip: str | None = None) -> str | None:
    """
    SHA-256 over the normalised signals plus the /24 of the IP. Returns None when
    there isn't enough signal to be even weakly useful (< 3 keys).
    """
    clean = normalise_signals(signals)
    if len(clean) < 3:
        return None
    ip_prefix = ""
    if ip and ip.count(".") == 3:
        ip_prefix = ".".join(ip.split(".")[:3])
    payload = "|".join(f"{k}={clean[k]}" for k in _STABLE_KEYS if k in clean)
    payload = f"{payload}|ip24={ip_prefix}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def confidence_of(signals: dict[str, str]) -> float:
    """0..1, purely a function of how many stable keys we got. Never enough alone."""
    clean = normalise_signals(signals)
    return round(min(len(clean), len(_STABLE_KEYS)) / (len(_STABLE_KEYS) + 2), 3)
