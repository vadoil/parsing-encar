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
    retry_min_wait_sec: float = 5.0
    retry_max_wait_sec: float = 60.0
    headless_browser: bool = True

    min_delay_sec: float = 2.0
    max_delay_sec: float = 5.0
    min_model_delay_sec: float = 5.0
    max_model_delay_sec: float = 15.0

    # Pagination safety: how many list pages to fetch per model before giving up.
    # A single encar model can return up to ~1000+ cars (e.g. BMW X5 = 1102,
    # 56 pages at 20 items each). The actual stop condition is total_collected
    # >= reported Count, so this is just a backstop for missing/broken Count.
    # Default 200 covers ~4000 cars, comfortably over any realistic model.
    max_pages: int = 200
    page_size: int = 20

    # ── Scheduler (Phase 4) ────────────────────────────────────────────────
    # Number of buckets in the rotation. The bucket chosen for today is
    # ``(day_of_year - 1) % scheduler_bucket_count``. With 105 enabled
    # models and bucket_count=14 the daily slice is ~7-8 models; with
    # bucket_count=7 it is ~15. 14 is the safe default (daily work
    # comfortably under 12h even on a full backfill).
    scheduler_bucket_count: int = 14
    # After parsing a model, leave it alone for this many hours regardless
    # of which bucket today is. EncAr's "newest listings" window is ~24h;
    # 12h keeps incremental runs cheap without missing freshly-listed cars.
    scheduler_cooldown_hours: int = 12
    # Path to the JSON state file used by ``backfill --resume``. Must be
    # on a persistent volume (the parser container's ``/var/log`` is).
    backfill_state_path: str = "/var/log/backfill_state.json"
    # Path to the cached per-model Count used by ``plan`` dry-runs. Written
    # by ``plan --probe`` (slow but accurate) and read by every later run.
    plan_counts_cache: str = "/var/log/encar_counts.json"

    # ── Web viewer ────────────────────────────────────────────────────────
    # KRW → RUB rate used in the web table. 0.048 ≈ реальный курс начала
    # 2026. Позже заменим на ежедневную таблицу курсов; до тех пор это
    # константа, явно подписанная в UI.
    krw_to_rub_rate: float = 0.048
    # HTTP port for the web viewer (uvicorn). Used by Dockerfile / compose.
    web_port: int = 8090
    # Image proxy tunables. Cache is per-process in-memory; it survives across
    # requests but NOT across container restarts (that's fine — encar URLs are
    # stable and the proxy will re-fetch on first hit).
    img_proxy_timeout_sec: float = 10.0
    img_proxy_cache_max_entries: int = 2000
    img_proxy_cache_ttl_sec: int = 3600
    img_proxy_allowed_hosts: tuple[str, ...] = ("ci.encar.com", "img.encar.com")


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
