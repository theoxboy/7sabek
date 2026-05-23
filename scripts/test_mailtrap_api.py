#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def mask_token(value: str | None) -> str:
    if not value:
        return "MISSING"
    if len(value) <= 6:
        return "SET length=%d" % len(value)
    return f"SET length={len(value)} preview={value[:3]}***{value[-3:]}"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    token = (os.getenv("MAILTRAP_API_TOKEN") or "").strip()
    api_base = (
        (os.getenv("MAILTRAP_API_BASE") or "").strip()
        or "https://send.api.mailtrap.io/api/send"
    )
    mail_from = (os.getenv("MAIL_FROM") or "").strip()
    mail_to = (os.getenv("MAIL_TEST_TO") or "").strip()

    print("[mailtrap-api-test] Env status")
    print(f"  MAILTRAP_API_TOKEN={mask_token(token)}")
    print(f"  MAILTRAP_API_BASE={api_base or 'MISSING'}")
    print(f"  MAIL_FROM={mail_from or 'MISSING'}")
    print(f"  MAIL_TEST_TO={mail_to or 'MISSING'}")

    missing = []
    if not token:
        missing.append("MAILTRAP_API_TOKEN")
    if not mail_from:
        missing.append("MAIL_FROM")
    if not mail_to:
        missing.append("MAIL_TEST_TO")

    if missing:
        print("[mailtrap-api-test] FAIL missing required env vars:")
        for key in missing:
            print(f"  - {key}")
        return 2

    payload = {
        "from": {"email": mail_from, "name": "7sabek"},
        "to": [{"email": mail_to}],
        "subject": "7sabek Mailtrap API diagnostic",
        "text": "Mailtrap API diagnostic email from test script.",
        "category": "Password Reset",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    print("[mailtrap-api-test] Sending request...")
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(api_base, json=payload, headers=headers)
        print(f"[mailtrap-api-test] HTTP {response.status_code}")
        if 200 <= response.status_code < 300:
            print("[mailtrap-api-test] SUCCESS")
            return 0

        body = response.text.strip()
        if len(body) > 800:
            body = body[:800] + "...<truncated>"
        print("[mailtrap-api-test] FAIL response body:")
        print(body)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[mailtrap-api-test] FAIL {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
