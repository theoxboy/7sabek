from __future__ import annotations

import re


def _normalize_name(value: str) -> str:
    lowered = value.strip().casefold()
    lowered = (
        lowered.replace("à", "a")
        .replace("á", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("ç", "c")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("ì", "i")
        .replace("í", "i")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ò", "o")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("ú", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ý", "y")
        .replace("ÿ", "y")
    )
    lowered = lowered.replace("—", " ").replace("-", " ").replace("_", " ").replace("/", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def is_virtual_parent_envelope_name(name: str) -> bool:
    normalized = _normalize_name(name)
    # Parent-only planning envelopes (not real budget buckets).
    return normalized in {
        "المرونة",
        "flexibilite",
        "flexibility",
        "flex",
    }

