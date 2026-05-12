from __future__ import annotations

import re
from pathlib import Path

from app.services.category_catalog import category_key_from_name


_TS_ALIAS_PATTERN = re.compile(
    r"const LEGACY_ALIAS_TO_KEY: Record<string, string> = \{(?P<body>.*?)\n\};",
    re.S,
)
_TS_ITEM_PATTERN = re.compile(r'^\s*(?:"([^"]+)"|([a-zA-Z0-9_\u0600-\u06ff]+))\s*:\s*"([^"]+)"\s*,?\s*$')


def _load_frontend_aliases() -> dict[str, str]:
    ts_path = Path("floussy-web/src/lib/categoryCatalog.ts")
    content = ts_path.read_text(encoding="utf-8")
    block_match = _TS_ALIAS_PATTERN.search(content)
    if block_match is None:
        raise AssertionError("LEGACY_ALIAS_TO_KEY block not found in frontend categoryCatalog.ts")
    body = block_match.group("body")
    aliases: dict[str, str] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item_match = _TS_ITEM_PATTERN.match(line)
        if item_match is None:
            continue
        quoted_key, bare_key, value = item_match.groups()
        key = quoted_key or bare_key
        aliases[key] = value
    return aliases


def test_backend_and_frontend_aliases_are_consistent() -> None:
    frontend_aliases = _load_frontend_aliases()
    assert frontend_aliases, "No frontend aliases extracted"
    for alias, expected_key in frontend_aliases.items():
        assert (
            category_key_from_name(alias) == expected_key
        ), f"Alias mismatch for '{alias}': backend={category_key_from_name(alias)} frontend={expected_key}"

