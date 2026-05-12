from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request


def get_current_user_id(request: Request) -> UUID:
    raw_user_id = request.headers.get("X-User-Id")
    if not raw_user_id:
        raise HTTPException(status_code=400, detail="X-User-Id header is required")
    try:
        return UUID(raw_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="X-User-Id must be a valid UUID"
        ) from exc
