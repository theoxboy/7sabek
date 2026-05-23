from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Floussy"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    cookie_secure: bool = True
    backup_storage_dir: str = "/opt/backups/floussy"
    backup_retention_count: int = 1
    backup_schedule_days: int = 15
    backup_cron_token: Optional[str] = None
    gamification_cron_token: Optional[str] = None
    mail_provider: str = "mailtrap"
    mail_from: str = "noreply@floussy.online"
    app_base_url: str = "https://7sabek.ma"
    password_reset_path: str = "/reset-password"
    password_reset_token_ttl_minutes: int = 5
    password_reset_delivery_retries: int = 3
    superadmin_email: Optional[str] = None
    superadmin_password: Optional[str] = None
    superadmin_currency: str = "MAD"
    superadmin_sweep_interval_days: int = 30
    superadmin_password_reset_code: str = ""
    superadmin_password_reset_first_name: str = ""
    mailtrap_api_token: Optional[str] = None
    mailtrap_api_base: str = "https://send.api.mailtrap.io/api/send"
    recaptcha_secret_key: Optional[str] = None
    recaptcha_enabled: bool = True
    enable_passkeys: bool = False
    passkeys_allow_all: bool = False
    passkeys_allowed_emails: str = ""
    passkey_rp_id: str = "7sabek.ma"
    passkey_rp_origin: str = "https://7sabek.ma"
    passkey_rp_name: str = "7sabek"
    passkey_challenge_ttl_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", case_sensitive=False
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_passkeys_allowed_emails(raw_value: str) -> set[str]:
    return {
        part.strip().lower()
        for part in raw_value.split(",")
        if part and part.strip()
    }


def is_passkeys_enabled_for_email(email: str | None) -> bool:
    settings = get_settings()
    if not settings.enable_passkeys:
        return False
    if settings.passkeys_allow_all:
        return True
    normalized = (email or "").strip().lower()
    if not normalized:
        return False
    return normalized in get_passkeys_allowed_emails(settings.passkeys_allowed_emails)
