from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Floussy"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    cookie_secure: bool = False
    backup_storage_dir: str = "/opt/backups/floussy"
    backup_retention_count: int = 1
    backup_schedule_days: int = 15
    backup_cron_token: Optional[str] = None
    gamification_cron_token: Optional[str] = None
    mail_provider: str = "mailtrap"
    mail_from: str = "hello@demomailtrap.co"
    app_base_url: str = "https://7sabek.ma"
    password_reset_path: str = "/reset-password"
    password_reset_token_ttl_minutes: int = 5
    password_reset_delivery_retries: int = 3
    superadmin_email: Optional[str] = None
    superadmin_password: Optional[str] = None
    superadmin_currency: str = "MAD"
    superadmin_sweep_interval_days: int = 30
    superadmin_password_reset_code: str = "4303"
    superadmin_password_reset_first_name: str = "OMAR"
    mailtrap_api_token: Optional[str] = "19dccfb642a"
    mailtrap_api_base: str = "https://send.api.mailtrap.io/api/send"
    recaptcha_secret_key: Optional[str] = "6LciJPMsAAAAADCbwpN6VAbANgR9z6Oe3vmwZ2u7"
    recaptcha_enabled: bool = True
    recaptcha_min_score: float = 0.5

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", case_sensitive=False
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
