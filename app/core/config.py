from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.config")


class Settings(BaseSettings):
    app_name: str = "Floussy"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    cookie_secure: Optional[bool] = None
    session_max_age_days: int = 30
    backup_storage_dir: str = "/opt/backups/floussy"
    backup_retention_count: int = 1
    backup_schedule_days: int = 15
    backup_cron_token: Optional[str] = None
    gamification_cron_token: Optional[str] = None
    mail_provider: str = "log"
    mail_from: str = "noreply@floussy.local"
    app_base_url: str = "http://127.0.0.1:3000"
    password_reset_path: str = "/reset-password"
    password_reset_token_ttl_minutes: int = 5
    password_reset_delivery_retries: int = 3
    superadmin_password_reset_code: str = "4303"
    superadmin_password_reset_first_name: str = "OMAR"
    mailtrap_api_token: Optional[str] = None
    mailtrap_api_base: str = "https://send.api.mailtrap.io/api/send"

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", case_sensitive=False
    )


    @property
    def resolved_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment.strip().lower() not in {"local", "development", "dev", "test"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.jwt_secret == "change-me":
        env = settings.environment.strip().lower()
        if env not in {"local", "development", "dev", "test"}:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong secret in non-local environments. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        warnings.warn(
            "JWT_SECRET is set to the default 'change-me'. "
            "This is only acceptable for local development.",
            stacklevel=2,
        )
    return settings
