from encar_parser.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "500")

    s = Settings()

    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.log_level == "DEBUG"
    assert s.rate_limit_per_hour == 500


def test_settings_defaults():
    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.request_timeout_sec == 30
    assert s.retry_max_attempts == 3
    assert s.headless_browser is True
