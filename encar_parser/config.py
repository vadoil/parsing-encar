from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://encar:encar@localhost:5432/encar"
    log_level: str = "INFO"
    sentry_dsn: str = ""

    # encar API endpoints (overridable if encar changes them)
    api_list_base: str = "https://api.encar.com/search/car/list/general"
    api_detail_template: str = "https://api.encar.com/v1/readside/vehicle/{encar_id}"
    encar_referer: str = "https://www.encar.com/fc/fc_carsearchlist.do"

    rate_limit_per_hour: int = 1200
    request_timeout_sec: int = 30
    retry_max_attempts: int = 3
    headless_browser: bool = True

    min_delay_sec: float = 2.0
    max_delay_sec: float = 5.0
    min_model_delay_sec: float = 5.0
    max_model_delay_sec: float = 15.0


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset the cached settings (for tests)."""
    global _settings
    _settings = None
