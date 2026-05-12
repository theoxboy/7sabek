from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.admin_activity import create_admin_log
from app.core.config import get_settings
from app.core.logging import LOG_FILE_PATH
from app.core.rate_limit import enforce_rate_limit, get_client_ip
from app.models import User

router = APIRouter(prefix="/logs")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _mask_last4(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return trimmed
    if len(trimmed) <= 4:
        return "****"
    return f"{'*' * max(len(trimmed) - 4, 4)}{trimmed[-4:]}"


def _redact_line(line: str) -> str:
    import re

    patterns = [
        (r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]"),
        (r"(?i)(cookie\s*[:=]\s*)([^\n]+)", r"\1[REDACTED]"),
        (r"(?i)\b(token|access_token|refresh_token)\b\s*[:=]\s*([^\s,;]+)", r"\1=[REDACTED]"),
        (
            r"(?i)\b(account_id)\b\s*[:=]\s*([a-z0-9-]+)",
            lambda m: f"{m.group(1)}={_mask_last4(m.group(2))}",
        ),
        (
            r"(?i)\b(email)\b\s*[:=]\s*([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})",
            lambda m: f"{m.group(1)}={_mask_last4(m.group(2))}",
        ),
        (
            r"(?i)\b(phone|tel|mobile)\b\s*[:=]\s*(\+?[0-9][0-9 \-().]{5,}[0-9])",
            lambda m: f"{m.group(1)}={_mask_last4(re.sub(r'[^0-9+]', '', m.group(2)))}",
        ),
        (
            r"(?<!\d)((?:\d{1,3}\.){3}\d{1,3})(?!\d)",
            lambda m: _mask_last4(m.group(1)),
        ),
    ]

    redacted = line
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def _prod_error(detail: str, correlation_id: str) -> dict:
    return {
        "code": "INTERNAL_ERROR",
        "message": detail,
        "correlation_id": correlation_id,
    }


@router.get("/backend")
async def read_backend_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    lines: int = Query(50, ge=1, le=100),
    page: int = Query(1, ge=1, le=1000),
) -> dict:
    _require_superadmin(current_user)
    await enforce_rate_limit(
        db=db,
        request=request,
        key_prefix=f"logs:backend:{current_user.id}",
        limit=30,
        window_seconds=60,
    )

    correlation_id = str(uuid4())
    log_path = Path(LOG_FILE_PATH)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    try:
        tail = deque(maxlen=2000)
        with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                tail.append(line.rstrip("\n"))
    except OSError as exc:
        settings = get_settings()
        if settings.environment.lower() in {"production", "prod"}:
            raise HTTPException(
                status_code=500,
                detail=_prod_error(
                    "Internal error. Contact support with the correlation id.",
                    correlation_id,
                ),
            ) from exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    all_lines = list(tail)
    total = len(all_lines)
    start = max(total - (page * lines), 0)
    end = total - ((page - 1) * lines)
    page_lines = all_lines[start:end]
    page_lines = [_redact_line(line) for line in page_lines]

    await create_admin_log(
        db=db,
        event_type="logs.backend.read",
        status="info",
        actor_email=current_user.email,
        actor_ip=get_client_ip(request),
        message=(
            f"Read backend logs page={page} lines={lines} total={total} "
            f"at={datetime.now(timezone.utc).isoformat()} correlation_id={correlation_id}"
        ),
    )

    return {
        "lines": page_lines,
        "pagination": {
            "page": page,
            "lines": lines,
            "total_lines": total,
            "has_next_page": start > 0,
        },
        "correlation_id": correlation_id,
    }
