#!/usr/bin/env python3
from __future__ import annotations

import os
import smtplib
import socket
from email.message import EmailMessage
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def mask_secret(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def pick(*keys: str) -> str:
    for key in keys:
        val = os.getenv(key)
        if val and val.strip():
            return val.strip()
    return ""


def as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    host = pick("SMTP_HOST", "MAILTRAP_HOST")
    port = as_int(pick("SMTP_PORT", "MAILTRAP_PORT") or "587", 587)
    username = pick("SMTP_USERNAME", "MAILTRAP_USERNAME", "MAILTRAP_USER", "MAILTRAP_LOGIN")
    password = pick("SMTP_PASSWORD", "MAILTRAP_PASSWORD", "MAILTRAP_PASS")
    sender = pick("SMTP_FROM", "MAIL_FROM", "EMAIL_FROM")
    to_email = pick("MAIL_TEST_TO")

    print("[smtp-test] Loaded SMTP config")
    print(f"  host={host or '<missing>'}")
    print(f"  port={port}")
    print(f"  username={username or '<missing>'}")
    print(f"  password={mask_secret(password)}")
    print(f"  from={sender or '<missing>'}")
    print(f"  to={to_email or '<missing>'}")

    missing = []
    if not host:
        missing.append("SMTP_HOST/MAILTRAP_HOST")
    if not username:
        missing.append("SMTP_USERNAME/MAILTRAP_USERNAME")
    if not password:
        missing.append("SMTP_PASSWORD/MAILTRAP_PASSWORD")
    if not sender:
        missing.append("SMTP_FROM/MAIL_FROM/EMAIL_FROM")
    if not to_email:
        missing.append("MAIL_TEST_TO")

    if missing:
        print("[smtp-test] FAIL missing required env vars:")
        for key in missing:
            print(f"  - {key}")
        return 2

    msg = EmailMessage()
    msg["Subject"] = "7sabek SMTP diagnostic"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content("SMTP diagnostic email from local test script.")

    print("[smtp-test] Connecting...")
    try:
        with smtplib.SMTP(host=host, port=port, timeout=15) as server:
            server.ehlo()
            if port == 587:
                print("[smtp-test] STARTTLS...")
                server.starttls()
                server.ehlo()
            print("[smtp-test] Logging in...")
            server.login(username, password)
            print("[smtp-test] Sending test email...")
            server.send_message(msg)
        print("[smtp-test] SUCCESS email sent")
        return 0
    except (smtplib.SMTPException, socket.error, OSError) as exc:
        print(f"[smtp-test] FAIL {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
