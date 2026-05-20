from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_token(
    subject: str,
    expires_delta: timedelta,
    token_type: str = "access",
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": now + expires_delta,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "type": token_type,
        "iss": settings.app_base_url,
        "aud": settings.app_base_url,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> str:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
    iss = payload.get("iss")
    if iss is not None and iss != settings.app_base_url:
        raise ValueError("Invalid issuer")
    aud = payload.get("aud")
    if aud is not None and aud != settings.app_base_url:
        raise ValueError("Invalid audience")
    token_type = payload.get("type", "access")
    if token_type != expected_type:
        raise ValueError(f"Expected {expected_type} token, got {token_type}")
    subject = payload.get("sub")
    if not subject:
        raise ValueError("Missing subject")
    return subject
