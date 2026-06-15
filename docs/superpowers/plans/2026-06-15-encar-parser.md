# Enc Car Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python parser for encar.com that collects car listings from saved filter URLs (100+ models) and stores them in PostgreSQL, with the data destined for a custom car catalog site.

**Architecture:** Async Python with `httpx` (primary) and `Playwright` (fallback) fetchers, SQLAlchemy + Alembic for persistence, structlog for logging, scheduled via cron in Docker. Configuration in YAML. Hybrid fetcher pattern allows proxy integration later.

**Tech Stack:**
- Python 3.11+
- httpx (async HTTP), Playwright (browser fallback)
- SQLAlchemy 2.x + Alembic + asyncpg
- Pydantic v2 + pydantic-settings
- structlog (JSON logging)
- tenacity (retries)
- pytest + pytest-asyncio + respx (HTTP mocking)
- Typer (CLI)
- uv (package manager)
- Docker + docker-compose

**Spec:** `docs/superpowers/specs/2026-06-15-encar-parser-design.md`

---

## File Structure

```
encar-parser/
├── encar_parser/
│   ├── __init__.py
│   ├── cli.py                 # Typer CLI: run / sync / migrate / record-fixtures
│   ├── config.py              # pydantic-settings
│   ├── translations.py        # KO → RU dictionaries
│   ├── encar_url.py           # build_action() from ModelConfig
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py         # async engine, session factory
│   │   ├── models.py          # SearchModel, Car, Run, CarModelMatch
│   │   ├── repository.py      # CRUD: upsert_car, link_to_model, etc.
│   │   └── migrations/        # alembic
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py            # Fetcher Protocol, FetcherResponse
│   │   ├── api.py             # ApiFetcher (httpx)
│   │   ├── browser.py         # BrowserFetcher (Playwright)
│   │   └── factory.py         # FetcherFactory with fallback logic
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── list_page.py       # parse_search_list(json) -> list[int]
│   │   └── details.py         # parse_car_detail(json) -> Car
│   ├── pipeline.py            # main loop
│   ├── scheduler.py           # 3-day rotation
│   └── utils/
│       ├── __init__.py
│       ├── log.py             # structlog setup
│       ├── rate_limit.py      # token bucket
│       └── ua.py              # User-Agent pool
├── models.yaml
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── search_list_bmw_x5.json
│   │   ├── car_detail_42131435.json
│   │   └── models_example.yaml
│   ├── unit/
│   │   ├── test_encar_url.py
│   │   ├── test_translations.py
│   │   ├── test_parsers.py
│   │   ├── test_repository.py
│   │   ├── test_scheduler.py
│   │   └── test_ua.py
│   ├── integration/
│   │   ├── test_pipeline.py
│   │   └── test_retry.py
│   └── e2e/
│       └── test_smoke_live.py
├── pyproject.toml
├── Makefile
├── .env.example
├── .gitignore
├── Dockerfile
└── docker-compose.yml
```

---

## Task 1: Project skeleton & tooling

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `encar_parser/__init__.py`
- Create: `encar_parser/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Initialize uv project**

Run:
```bash
cd /Users/mac/ClaudeProjects/parsing\ encar
uv init --no-readme --no-pin-python --package
```

Expected: `pyproject.toml`, `encar_parser/`, `tests/` created.

- [ ] **Step 2: Configure pyproject.toml with dependencies**

Replace `pyproject.toml` with:

```toml
[project]
name = "encar-parser"
version = "0.1.0"
description = "Parser for encar.com car listings"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "playwright>=1.47",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "structlog>=24.1",
    "tenacity>=9.0",
    "typer>=0.12",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "ruff>=0.5",
    "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["encar_parser"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --strict-markers"
markers = [
    "live: tests that hit real encar.com",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "A", "C4", "PT", "RET", "SIM"]
```

- [ ] **Step 3: Install dependencies**

Run:
```bash
uv sync --extra dev
uv run playwright install chromium
```

Expected: Dependencies installed, Chromium downloaded.

- [ ] **Step 4: Create Makefile**

Create `Makefile`:

```makefile
.PHONY: install test test-live lint format migrate run sync

install:
	uv sync --extra dev
	uv run playwright install chromium

test:
	uv run pytest tests/unit tests/integration

test-live:
	uv run pytest tests/e2e -m live

lint:
	uv run ruff check .
	uv run mypy encar_parser

format:
	uv run ruff format .

migrate:
	uv run alembic upgrade head

run:
	uv run python -m encar_parser run

sync:
	uv run python -m encar_parser sync

record-fixtures:
	uv run python -m encar_parser record-fixtures
```

- [ ] **Step 5: Create .gitignore**

Create `.gitignore`:

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.env
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
.coverage
*.log
tests/fixtures/live_*.json
```

- [ ] **Step 6: Create .env.example**

Create `.env.example`:

```
DATABASE_URL=postgresql+asyncpg://encar:encar@localhost:5432/encar
LOG_LEVEL=INFO
SENTRY_DSN=
RATE_LIMIT_PER_HOUR=1200
REQUEST_TIMEOUT_SEC=30
RETRY_MAX_ATTEMPTS=3
HEADLESS_BROWSER=true
```

- [ ] **Step 7: Verify it imports**

Run:
```bash
uv run python -c "import encar_parser; print('ok')"
```

Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git init
git add .
git commit -m "chore: project skeleton with uv and dev tooling"
```

---

## Task 2: Configuration & logging

**Files:**
- Create: `encar_parser/config.py`
- Create: `encar_parser/utils/log.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test for config**

Create `tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'encar_parser.config'`

- [ ] **Step 3: Implement config module**

Create `encar_parser/config.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Implement structlog setup**

Create `encar_parser/utils/__init__.py` (empty file).

Create `encar_parser/utils/log.py`:

```python
from __future__ import annotations

import logging
import sys

import structlog

from encar_parser.config import get_settings


def setup_logging() -> None:
    """Configure structlog for JSON output to stdout."""
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given name."""
    return structlog.get_logger(name)
```

- [ ] **Step 6: Commit**

```bash
git add encar_parser/config.py encar_parser/utils/ tests/unit/test_config.py
git commit -m "feat: config and structlog setup"
```

---

## Task 3: Translations module (KO → RU)

**Files:**
- Create: `encar_parser/translations.py`
- Create: `tests/unit/test_translations.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_translations.py`:

```python
import pytest

from encar_parser.translations import (
    translate_fuel,
    translate_transmission,
    translate_color,
    translate_import_type,
)


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("가솔린", "Бензин"),
        ("디젤", "Дизель"),
        ("하이브리드", "Гибрид"),
        ("전기", "Электро"),
        ("LPG", "Газ"),
        ("가스", "Газ"),
    ],
)
def test_translate_fuel(korean, russian):
    assert translate_fuel(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("오토", "Автомат"),
        ("수동", "Механика"),
        ("CVT", "Вариатор"),
        ("DCT", "Робот"),
        ("자동", "Автомат"),
    ],
)
def test_translate_transmission(korean, russian):
    assert translate_transmission(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("검정색", "Чёрный"),
        ("흰색", "Белый"),
        ("회색", "Серый"),
        ("은색", "Серебристый"),
        ("파란색", "Синий"),
        ("빨간색", "Красный"),
    ],
)
def test_translate_color(korean, russian):
    assert translate_color(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("정식수입", "Официальный"),
        ("병행수입", "Параллельный"),
    ],
)
def test_translate_import_type(korean, russian):
    assert translate_import_type(korean) == russian


def test_translate_unknown_fuel_returns_original():
    """Unknown values pass through unchanged for visibility."""
    assert translate_fuel("수소") == "수소"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_translations.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement translations module**

Create `encar_parser/translations.py`:

```python
"""Korean → Russian translation dictionaries for encar fields."""

FUEL_KO_TO_RU: dict[str, str] = {
    "가솔린": "Бензин",
    "디젤": "Дизель",
    "하이브리드": "Гибрид",
    "전기": "Электро",
    "LPG": "Газ",
    "가스": "Газ",
    "수소": "Водород",
}

TRANSMISSION_KO_TO_RU: dict[str, str] = {
    "오토": "Автомат",
    "자동": "Автомат",
    "수동": "Механика",
    "CVT": "Вариатор",
    "DCT": "Робот",
    "로봇": "Робот",
}

COLOR_KO_TO_RU: dict[str, str] = {
    "검정색": "Чёрный",
    "흰색": "Белый",
    "회색": "Серый",
    "은색": "Серебристый",
    "파란색": "Синий",
    "빨간색": "Красный",
    "노란색": "Жёлтый",
    "녹색": "Зелёный",
    "갈색": "Коричневый",
    "보라색": "Фиолетовый",
}

IMPORT_TYPE_KO_TO_RU: dict[str, str] = {
    "정식수입": "Официальный",
    "병행수입": "Параллельный",
}


def translate_fuel(value: str) -> str:
    """Translate fuel type from Korean to Russian. Unknown values pass through."""
    return FUEL_KO_TO_RU.get(value, value)


def translate_transmission(value: str) -> str:
    """Translate transmission type from Korean to Russian. Unknown values pass through."""
    return TRANSMISSION_KO_TO_RU.get(value, value)


def translate_color(value: str) -> str:
    """Translate color from Korean to Russian. Unknown values pass through."""
    return COLOR_KO_TO_RU.get(value, value)


def translate_import_type(value: str) -> str:
    """Translate import type from Korean to Russian. Unknown values pass through."""
    return IMPORT_TYPE_KO_TO_RU.get(value, value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_translations.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/translations.py tests/unit/test_translations.py
git commit -m "feat: KO→RU translation dictionaries"
```

---

## Task 4: encar_url builder (YAML config → action JSON)

**Files:**
- Create: `encar_parser/encar_url.py`
- Create: `models.yaml`
- Create: `tests/unit/test_encar_url.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_encar_url.py`:

```python
import pytest

from encar_parser.encar_url import ModelConfig, build_action, build_url


def test_build_action_minimal():
    cfg = ModelConfig(slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5")
    action = build_action(cfg)

    assert "Manufacturer.BMW" in action["action"]
    assert "ModelGroup.X5" in action["action"]
    assert "Hidden.N" in action["action"]
    assert "CarType.N" in action["action"]
    assert action["sort"] == "ModifiedDate"
    assert action["limit"] == 20
    assert action["page"] == 1


def test_build_action_with_year_range():
    cfg = ModelConfig(
        slug="x", name="x",
        manufacturer="BMW", model="X5 (G05)",
        year_from=2018, year_to=2025,
    )
    action = build_action(cfg)
    payload = action["action"]
    assert "Model.X5" in payload
    # Year range encoded in action
    assert "2018" in payload or "Year" in payload


def test_build_action_with_optional_filters():
    cfg = ModelConfig(
        slug="x", name="x",
        manufacturer="Kia", model_group="Sportage", model="Sportage",
        fuel="hybrid", transmission="automatic", body_type="SUV",
    )
    action = build_action(cfg)
    assert "Fuel.hybrid" in action["action"]
    assert "Transmission.automatic" in action["action"]
    assert "BodyType.SUV" in action["action"]


def test_build_url_returns_full_url():
    cfg = ModelConfig(slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5")
    url = build_url(cfg)
    assert url.startswith("https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!")
    assert "Manufacturer.BMW" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_encar_url.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement encar_url module**

Create `encar_parser/encar_url.py`:

```python
"""Build encar.com search URLs from a ModelConfig."""

from __future__ import annotations

import json
import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field

EncCarType = Literal["for", "new", "domestic"]  # used/foreign-new/domestic
SortOrder = Literal["ModifiedDate", "PriceAsc", "PriceDesc", "MileageAsc", "YearDesc"]


class ModelConfig(BaseModel):
    """Configuration for a single search model (saved filter)."""

    slug: str
    name: str
    enabled: bool = True
    priority: int = 100

    manufacturer: str | None = None
    model_group: str | None = None
    model: str | None = None

    year_from: int | None = None
    year_to: int | None = None

    fuel: str | None = None  # gasoline, diesel, hybrid, electric, lpg
    transmission: str | None = None  # automatic, manual, cvt
    body_type: str | None = None  # sedan, suv, etc.

    price_from: int | None = None
    price_to: int | None = None
    mileage_to: int | None = None

    car_type: EncCarType = "for"
    sort: SortOrder = "ModifiedDate"
    limit: int = Field(default=20, ge=1, le=100)


def _escape(value: str) -> str:
    """Escape value for the S-expression filter string."""
    return value.replace(" ", "%20").replace("(", "_").replace(")", "_")


def _year_range_clause(cfg: ModelConfig) -> str | None:
    if cfg.year_from is None and cfg.year_to is None:
        return None
    parts = []
    if cfg.year_from is not None:
        parts.append(f"YearFrom.{cfg.year_from}")
    if cfg.year_to is not None:
        parts.append(f"YearTo.{cfg.year_to}")
    return "(._.".join(parts) + ".)"


def build_action(cfg: ModelConfig) -> dict:
    """Build the action JSON dict that encar expects in the URL hash.

    The action is an S-expression-style filter:
    (And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5%20(G05_).))))
    """
    parts: list[str] = ["And", "Hidden.N", "CarType.N"]

    if cfg.manufacturer:
        parts.append(f"Manufacturer.{_escape(cfg.manufacturer)}")
    if cfg.model_group:
        parts.append(f"ModelGroup.{_escape(cfg.model_group)}")
    if cfg.model:
        parts.append(f"Model.{_escape(cfg.model)}")
    if cfg.fuel:
        parts.append(f"Fuel.{_escape(cfg.fuel)}")
    if cfg.transmission:
        parts.append(f"Transmission.{_escape(cfg.transmission)}")
    if cfg.body_type:
        parts.append(f"BodyType.{_escape(cfg.body_type)}")

    year_clause = _year_range_clause(cfg)
    if year_clause:
        parts.append(year_clause)

    action_str = "(" + "._.".join(parts) + ".)"

    return {
        "action": action_str,
        "toggle": {"5": 1},
        "layer": "",
        "sort": cfg.sort,
        "page": 1,
        "limit": cfg.limit,
        "searchKey": "",
        "loginCheck": False,
    }


def build_url(cfg: ModelConfig) -> str:
    """Build a full encar.com search URL for the given config."""
    action = build_action(cfg)
    hash_payload = json.dumps(action, ensure_ascii=False, separators=(",", ":"))
    encoded_hash = urllib.parse.quote(hash_payload, safe="")
    return f"https://www.encar.com/fc/fc_carsearchlist.do?carType={cfg.car_type}#!{encoded_hash}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_encar_url.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Create models.yaml with examples**

Create `models.yaml`:

```yaml
models:
  - slug: bmw-x5-g05
    name: "BMW X5 (G05)"
    enabled: true
    priority: 10
    manufacturer: BMW
    model_group: X5
    model: "X5 (G05)"
    year_from: 2018
    year_to: 2025
    sort: ModifiedDate

  - slug: kia-sportage-nq5
    name: "Kia Sportage (NQ5)"
    enabled: true
    priority: 20
    manufacturer: Kia
    model_group: Sportage
    model: "Sportage (NQ5)"
    year_from: 2022
    fuel: hybrid

  - slug: hyundai-sonata-dn8
    name: "Hyundai Sonata (DN8)"
    enabled: true
    priority: 30
    manufacturer: Hyundai
    model_group: Sonata
    model: "Sonata (DN8)"
    year_from: 2019
    year_to: 2023
```

- [ ] **Step 6: Commit**

```bash
git add encar_parser/encar_url.py tests/unit/test_encar_url.py models.yaml
git commit -m "feat: encar_url builder and example models.yaml"
```

---

## Task 5: SQLAlchemy models

**Files:**
- Create: `encar_parser/db/__init__.py`
- Create: `encar_parser/db/session.py`
- Create: `encar_parser/db/models.py`
- Create: `tests/unit/test_db_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_db_models.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from encar_parser.db.models import Car, CarModelMatch, Run, SearchModel


@pytest.mark.asyncio
async def test_can_create_all_tables():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)
    assert True  # no exception = success


@pytest.mark.asyncio
async def test_search_model_roundtrip():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        sm = SearchModel(
            slug="bmw-x5-g05",
            name="BMW X5 (G05)",
            encar_url="https://example.com",
            encar_action={"action": "(And.Hidden.N._.Manufacturer.BMW.)"},
            enabled=True,
            priority=10,
        )
        session.add(sm)
        await session.commit()
        await session.refresh(sm)
        assert sm.id is not None
        assert sm.created_at is not None


@pytest.mark.asyncio
async def test_car_and_match_linkage():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        sm = SearchModel(
            slug="x", name="X", encar_url="u", encar_action={}
        )
        car = Car(encar_id=42131435, brand="BMW", model="X5 (G05)")
        session.add_all([sm, car])
        await session.flush()
        match = CarModelMatch(search_model_id=sm.id, encar_id=car.encar_id)
        session.add(match)
        await session.commit()
        await session.refresh(match)
        assert match.first_matched_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_db_models.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add aiosqlite to dev deps**

Run:
```bash
uv add --optional dev aiosqlite
```

- [ ] **Step 4: Implement DB models**

Create `encar_parser/db/__init__.py`:

```python
"""Database layer: SQLAlchemy models, session, repository."""

from encar_parser.db.models import Car, CarModelMatch, Run, SearchModel

__all__ = ["Car", "CarModelMatch", "Run", "SearchModel"]
```

Create `encar_parser/db/session.py`:

```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from encar_parser.config import get_settings


def make_engine():
    """Create an async engine from the configured DATABASE_URL."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncSession:
    """FastAPI-style dependency. Returns a session."""
    return get_sessionmaker()()
```

Create `encar_parser/db/models.py`:

```python
"""SQLAlchemy ORM models for the encar parser."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchModel(Base):
    __tablename__ = "search_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    encar_url: Mapped[str] = mapped_column(Text, nullable=False)
    encar_action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matches: Mapped[list[CarModelMatch]] = relationship(
        back_populates="search_model", cascade="all, delete-orphan"
    )


class Car(Base):
    __tablename__ = "cars"

    encar_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    year_month: Mapped[date | None] = mapped_column(Date)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    displacement_cc: Mapped[int | None] = mapped_column(Integer)

    fuel_ru: Mapped[str | None] = mapped_column(Text)
    fuel_original: Mapped[str | None] = mapped_column(Text)
    transmission_ru: Mapped[str | None] = mapped_column(Text)
    transmission_orig: Mapped[str | None] = mapped_column(Text)
    body_type: Mapped[str | None] = mapped_column(Text)

    color_ru: Mapped[str | None] = mapped_column(Text)
    color_original: Mapped[str | None] = mapped_column(Text)

    seats: Mapped[int | None] = mapped_column(Integer)
    import_type_ru: Mapped[str | None] = mapped_column(Text)
    manufacturer_warranty: Mapped[str | None] = mapped_column(Text)

    liens_seizures: Mapped[str | None] = mapped_column(Text)
    accident_records: Mapped[int | None] = mapped_column(Integer)
    plate_number: Mapped[str | None] = mapped_column(Text)

    price_krw: Mapped[int | None] = mapped_column(BigInteger)
    photo_urls: Mapped[list[str] | None] = mapped_column(JSON)
    encar_detail_url: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    matches: Mapped[list[CarModelMatch]] = relationship(
        back_populates="car", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_cars_brand_model", "brand", "model"),
        Index("idx_cars_year_month", "year_month"),
        Index("idx_cars_price_krw", "price_krw"),
    )


class CarModelMatch(Base):
    __tablename__ = "car_model_matches"

    search_model_id: Mapped[int] = mapped_column(
        ForeignKey("search_models.id", ondelete="CASCADE"), primary_key=True
    )
    encar_id: Mapped[int] = mapped_column(
        ForeignKey("cars.encar_id", ondelete="CASCADE"), primary_key=True
    )
    first_matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    search_model: Mapped[SearchModel] = relationship(back_populates="matches")
    car: Mapped[Car] = relationship(back_populates="matches")

    __table_args__ = (Index("idx_matches_model", "search_model_id"),)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    models_planned: Mapped[int] = mapped_column(Integer, default=0)
    models_done: Mapped[int] = mapped_column(Integer, default=0)
    cars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    cars_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_db_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add encar_parser/db/ tests/unit/test_db_models.py pyproject.toml uv.lock
git commit -m "feat: SQLAlchemy models for cars, search_models, runs"
```

---

## Task 6: Repository layer

**Files:**
- Create: `encar_parser/db/repository.py`
- Create: `tests/unit/test_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_repository.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, SearchModel
from encar_parser.db.repository import (
    Repository,
    upsert_car,
    upsert_search_model,
    link_car_to_model,
    get_enabled_models,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
async def test_upsert_search_model_creates(session):
    sm = await upsert_search_model(
        session,
        slug="bmw-x5-g05",
        name="BMW X5 (G05)",
        encar_url="https://example.com",
        encar_action={"action": "x"},
    )
    assert sm.id is not None
    assert sm.slug == "bmw-x5-g05"


@pytest.mark.asyncio
async def test_upsert_search_model_updates(session):
    sm1 = await upsert_search_model(session, slug="x", name="X", encar_url="u1", encar_action={})
    sm2 = await upsert_search_model(session, slug="x", name="X (renamed)", encar_url="u2", encar_action={})
    assert sm1.id == sm2.id
    assert sm2.name == "X (renamed)"


@pytest.mark.asyncio
async def test_upsert_car_creates(session):
    car = await upsert_car(session, encar_id=42131435, brand="BMW", model="X5 (G05)")
    assert car.encar_id == 42131435


@pytest.mark.asyncio
async def test_upsert_car_updates_existing(session):
    await upsert_car(session, encar_id=1, brand="BMW", model="X5")
    car = await upsert_car(session, encar_id=1, brand="BMW", model="X5 (G05)", price_krw=100000)
    assert car.model == "X5 (G05)"
    assert car.price_krw == 100000


@pytest.mark.asyncio
async def test_link_car_to_model_idempotent(session):
    sm = await upsert_search_model(session, slug="x", name="X", encar_url="u", encar_action={})
    await upsert_car(session, encar_id=1, brand="B", model="M")
    await link_car_to_model(session, search_model_id=sm.id, encar_id=1)
    await link_car_to_model(session, search_model_id=sm.id, encar_id=1)  # second time
    from sqlalchemy import select, func
    result = await session.execute(
        select(func.count()).select_from(CarModelMatch)
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_get_enabled_models_returns_sorted(session):
    await upsert_search_model(session, slug="b", name="B", encar_url="u", encar_action={}, priority=20)
    await upsert_search_model(session, slug="a", name="A", encar_url="u", encar_action={}, priority=10)
    await upsert_search_model(session, slug="c", name="C", encar_url="u", encar_action={}, enabled=False, priority=5)
    models = await get_enabled_models(session)
    assert [m.slug for m in models] == ["a", "b"]  # c is disabled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement repository**

Create `encar_parser/db/repository.py`:

```python
"""Repository functions for the encar parser. Thin wrappers over SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from encar_parser.db.models import Car, CarModelMatch, SearchModel


async def upsert_search_model(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    encar_url: str,
    encar_action: dict[str, Any],
    enabled: bool = True,
    priority: int = 100,
) -> SearchModel:
    """Create or update a SearchModel identified by slug. Returns the model."""
    existing = await session.scalar(select(SearchModel).where(SearchModel.slug == slug))
    if existing is None:
        sm = SearchModel(
            slug=slug,
            name=name,
            encar_url=encar_url,
            encar_action=encar_action,
            enabled=enabled,
            priority=priority,
        )
        session.add(sm)
    else:
        existing.name = name
        existing.encar_url = encar_url
        existing.encar_action = encar_action
        existing.enabled = enabled
        existing.priority = priority
        sm = existing
    await session.commit()
    await session.refresh(sm)
    return sm


async def upsert_car(
    session: AsyncSession,
    *,
    encar_id: int,
    brand: str,
    model: str,
    **fields: Any,
) -> Car:
    """Create or update a Car. Pass any column name as a kwarg."""
    existing = await session.scalar(select(Car).where(Car.encar_id == encar_id))
    if existing is None:
        car = Car(encar_id=encar_id, brand=brand, model=model, **fields)
        session.add(car)
    else:
        existing.brand = brand
        existing.model = model
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.last_seen_at = datetime.now(timezone.utc)
        car = existing
    await session.commit()
    await session.refresh(car)
    return car


async def link_car_to_model(
    session: AsyncSession, *, search_model_id: int, encar_id: int
) -> None:
    """Create a (model, car) match if it does not exist; update last_matched_at."""
    existing = await session.scalar(
        select(CarModelMatch).where(
            CarModelMatch.search_model_id == search_model_id,
            CarModelMatch.encar_id == encar_id,
        )
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        session.add(
            CarModelMatch(
                search_model_id=search_model_id,
                encar_id=encar_id,
                first_matched_at=now,
                last_matched_at=now,
            )
        )
    else:
        existing.last_matched_at = now
    await session.commit()


async def get_enabled_models(session: AsyncSession) -> list[SearchModel]:
    """Return all enabled search models sorted by (priority, slug)."""
    result = await session.scalars(
        select(SearchModel)
        .where(SearchModel.enabled.is_(True))
        .order_by(SearchModel.priority, SearchModel.slug)
    )
    return list(result.all())


class Repository:
    """Convenience wrapper that bundles session + operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_search_model(self, **kwargs: Any) -> SearchModel:
        return await upsert_search_model(self.session, **kwargs)

    async def upsert_car(self, **kwargs: Any) -> Car:
        return await upsert_car(self.session, **kwargs)

    async def link_car_to_model(self, **kwargs: Any) -> None:
        await link_car_to_model(self.session, **kwargs)

    async def get_enabled_models(self) -> list[SearchModel]:
        return await get_enabled_models(self.session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_repository.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/db/repository.py tests/unit/test_repository.py
git commit -m "feat: repository layer with upsert and link operations"
```

---

## Task 7: Alembic migrations

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial.py`
- Create: `encar_parser/db/migrate.py`

- [ ] **Step 1: Initialize alembic**

Run:
```bash
uv run alembic init -t async alembic
```

Expected: `alembic/` directory and `alembic.ini` created.

- [ ] **Step 2: Configure alembic.ini**

Edit `alembic.ini`, change the `sqlalchemy.url` line to:

```ini
sqlalchemy.url =
```

(Empty — we read from .env in env.py.)

- [ ] **Step 3: Configure env.py**

Replace `alembic/env.py` with:

```python
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from encar_parser.config import get_settings
from encar_parser.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate initial migration**

Run:
```bash
uv run alembic revision --autogenerate -m "initial schema"
```

Expected: A new file under `alembic/versions/` like `xxxx_initial_schema.py`.

- [ ] **Step 5: Review generated migration**

Open the generated file in `alembic/versions/` and verify it contains all tables: `search_models`, `cars`, `car_model_matches`, `runs`.

If the file looks correct, proceed. If not, fix by hand.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: alembic setup with initial migration"
```

---

## Task 8: Fetcher Protocol & response types

**Files:**
- Create: `encar_parser/fetchers/base.py`
- Create: `encar_parser/fetchers/__init__.py`
- Create: `tests/unit/test_fetcher_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fetcher_protocol.py`:

```python
import pytest

from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse


@pytest.mark.asyncio
async def test_protocol_can_be_implemented():
    class MyFetcher:
        async def get(self, url: str) -> FetcherResponse:
            return FetcherResponse(url=url, body=b"hello", status=200)

        async def close(self) -> None:
            pass

    f: Fetcher = MyFetcher()
    resp = await f.get("https://example.com")
    assert resp.body == b"hello"
    assert resp.status == 200
    await f.close()


def test_fetcher_error_carries_url():
    err = FetcherError("boom", url="https://example.com", status=503)
    assert err.status == 503
    assert str(err) == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fetcher_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement Fetcher base**

Create `encar_parser/fetchers/__init__.py`:

```python
from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse

__all__ = ["Fetcher", "FetcherError", "FetcherResponse"]
```

Create `encar_parser/fetchers/base.py`:

```python
"""Abstract fetcher interface. All fetchers (api, browser) implement this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class FetcherResponse:
    """A response from a fetcher."""

    url: str
    body: bytes
    status: int
    headers: dict[str, str] | None = None

    def json(self) -> object:
        """Parse body as JSON. Raises on invalid JSON."""
        import json
        return json.loads(self.body)

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


class FetcherError(Exception):
    """Base exception for fetcher errors."""

    def __init__(self, message: str, *, url: str | None = None, status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


@runtime_checkable
class Fetcher(Protocol):
    """Protocol every fetcher must implement."""

    async def get(self, url: str, *, params: dict | None = None) -> FetcherResponse:
        """Fetch a URL. Returns the raw response. Raises FetcherError on failure."""
        ...

    async def close(self) -> None:
        """Release resources (HTTP client, browser, etc.)."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fetcher_protocol.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/fetchers/ tests/unit/test_fetcher_protocol.py
git commit -m "feat: Fetcher Protocol and response types"
```

---

## Task 9: HTTP API fetcher (httpx)

**Files:**
- Create: `encar_parser/fetchers/api.py`
- Create: `tests/unit/test_api_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_api_fetcher.py`:

```python
import httpx
import pytest
import respx

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_get_returns_response():
    respx.get("https://api.encar.com/search").mock(
        return_value=httpx.Response(200, json={"SearchResults": []})
    )
    async with ApiFetcher() as f:
        resp = await f.get("https://api.encar.com/search")
        assert resp.status == 200
        assert resp.json() == {"SearchResults": []}


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_raises_on_4xx():
    respx.get("https://api.encar.com/missing").mock(return_value=httpx.Response(404))
    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/missing")
        assert exc_info.value.status == 404


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_sends_user_agent_and_referer():
    route = respx.get("https://api.encar.com/x").mock(return_value=httpx.Response(200, json={}))
    async with ApiFetcher() as f:
        await f.get("https://api.encar.com/x", referer="https://www.encar.com/")
        sent = route.calls.last.request
        assert "User-Agent" in sent.headers
        assert sent.headers.get("referer") == "https://www.encar.com/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_api_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement ApiFetcher**

Create `encar_parser/fetchers/api.py`:

```python
"""HTTP fetcher using httpx. Primary fetcher for the encar parser."""

from __future__ import annotations

import random

import httpx

from encar_parser.config import get_settings
from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse
from encar_parser.utils.ua import USER_AGENTS


class ApiFetcher:
    """Fetches URLs via httpx with rotation of User-Agent and retry-safe headers."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._ua_pool = list(USER_AGENTS)

    async def __aenter__(self) -> "ApiFetcher":
        timeout = httpx.Timeout(self._settings.request_timeout_sec)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _next_ua(self) -> str:
        return random.choice(self._ua_pool)

    async def get(
        self, url: str, *, params: dict | None = None, referer: str | None = None
    ) -> FetcherResponse:
        if self._client is None:
            raise RuntimeError("ApiFetcher used outside `async with` context")

        headers = {
            "User-Agent": self._next_ua(),
        }
        if referer:
            headers["Referer"] = referer

        try:
            resp = await self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise FetcherError(f"Timeout: {e}", url=url) from e
        except httpx.HTTPError as e:
            raise FetcherError(f"HTTP error: {e}", url=url) from e

        if resp.status_code in (403, 429):
            raise FetcherError(
                f"Blocked: {resp.status_code}",
                url=url,
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise FetcherError(
                f"HTTP {resp.status_code}",
                url=url,
                status=resp.status_code,
            )

        return FetcherResponse(
            url=str(resp.url),
            body=resp.content,
            status=resp.status_code,
            headers=dict(resp.headers),
        )
```

- [ ] **Step 4: Create User-Agent pool**

Create `encar_parser/utils/ua.py`:

```python
"""Pool of realistic User-Agent strings to rotate through."""

USER_AGENTS: list[str] = [
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
```

- [ ] **Step 5: Add a test for ua.py**

Create `tests/unit/test_ua.py`:

```python
from encar_parser.utils.ua import USER_AGENTS


def test_ua_pool_has_at_least_10_entries():
    assert len(USER_AGENTS) >= 10


def test_ua_strings_look_realistic():
    for ua in USER_AGENTS:
        assert ua.startswith("Mozilla/5.0")
        assert any(b in ua for b in ("Chrome", "Safari", "Firefox", "Edg"))
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/unit -v`
Expected: PASS for api_fetcher, ua, and earlier tests

- [ ] **Step 7: Commit**

```bash
git add encar_parser/fetchers/api.py encar_parser/utils/ua.py tests/unit/test_api_fetcher.py tests/unit/test_ua.py
git commit -m "feat: ApiFetcher with httpx + UA pool"
```

---

## Task 10: Browser fetcher (Playwright)

**Files:**
- Create: `encar_parser/fetchers/browser.py`
- Create: `tests/unit/test_browser_fetcher.py` (with @pytest.mark.live)

- [ ] **Step 1: Write the failing test (skipped by default)**

Create `tests/unit/test_browser_fetcher.py`:

```python
import pytest

from encar_parser.fetchers.base import FetcherError


@pytest.mark.asyncio
@pytest.mark.live
async def test_browser_fetcher_smoke():
    """Smoke test against real encar.com. Skipped by default, run with -m live."""
    from encar_parser.fetchers.browser import BrowserFetcher

    async with BrowserFetcher() as f:
        resp = await f.get("https://www.encar.com/fc/fc_carsearchlist.do?carType=for")
        assert resp.status == 200
        assert b"encar" in resp.body.lower() or len(resp.body) > 1000
```

- [ ] **Step 2: Run test to verify it is collected (and skipped)**

Run: `uv run pytest tests/unit/test_browser_fetcher.py -v --collect-only`
Expected: 1 test collected, marked as skipped (no `live` marker passed)

- [ ] **Step 3: Implement BrowserFetcher**

Create `encar_parser/fetchers/browser.py`:

```python
"""Browser fetcher using Playwright. Used as fallback when ApiFetcher is blocked."""

from __future__ import annotations

import random

from playwright.async_api import async_playwright, Browser, BrowserContext

from encar_parser.config import get_settings
from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse
from encar_parser.utils.ua import USER_AGENTS


class BrowserFetcher:
    """Fetches URLs via headless Chromium. Slower but bypasses many bot checks."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserFetcher":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.headless_browser,
        )
        self._context = await self._browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="ko-KR",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def get(self, url: str, *, params: dict | None = None) -> FetcherResponse:
        if self._context is None:
            raise RuntimeError("BrowserFetcher used outside `async with` context")

        try:
            page = await self._context.new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for results to render
                await page.wait_for_load_state("networkidle", timeout=15000)
                body = await page.content()
                status = response.status if response else 0
            finally:
                await page.close()
        except Exception as e:
            raise FetcherError(f"Browser error: {e}", url=url) from e

        if status == 403 or status == 429:
            raise FetcherError(f"Blocked: {status}", url=url, status=status)
        if status >= 400:
            raise FetcherError(f"HTTP {status}", url=url, status=status)

        return FetcherResponse(url=url, body=body.encode("utf-8"), status=status)
```

- [ ] **Step 4: Verify it imports without error**

Run:
```bash
uv run python -c "from encar_parser.fetchers.browser import BrowserFetcher; print('ok')"
```

Expected: `ok` (Playwright binary must be installed — see Task 1)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/fetchers/browser.py tests/unit/test_browser_fetcher.py
git commit -m "feat: BrowserFetcher (Playwright) for fallback"
```

---

## Task 11: Fetcher factory with fallback

**Files:**
- Create: `encar_parser/fetchers/factory.py`
- Create: `tests/unit/test_fetcher_factory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fetcher_factory.py`:

```python
import pytest

from encar_parser.fetchers.base import FetcherError
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.fetchers.api import ApiFetcher


@pytest.mark.asyncio
async def test_fallback_uses_api_on_success():
    """When ApiFetcher returns 200, no fallback to browser."""
    primary = ApiFetcher()
    secondary = ApiFetcher()  # will not be called

    call_count = {"primary": 0, "secondary": 0}

    class CountingApi(ApiFetcher):
        async def get(self, url, **kwargs):
            call_count["primary"] += 1
            from encar_parser.fetchers.base import FetcherResponse
            return FetcherResponse(url=url, body=b"{}", status=200)

        async def close(self): pass

    class CountingSecondary(CountingApi):
        async def get(self, url, **kwargs):
            call_count["secondary"] += 1
            from encar_parser.fetchers.base import FetcherResponse
            return FetcherResponse(url=url, body=b"{}", status=200)

        async def close(self): pass

    ff = FallbackFetcher(primary=CountingApi(), secondary=CountingSecondary())
    try:
        resp = await ff.get("https://example.com")
    finally:
        await ff.close()
    assert resp.status == 200
    assert call_count["primary"] == 1
    assert call_count["secondary"] == 0


@pytest.mark.asyncio
async def test_fallback_falls_back_on_403():
    """When primary raises FetcherError with 403, try secondary."""
    from encar_parser.fetchers.base import FetcherResponse

    class FailingPrimary(ApiFetcher):
        async def get(self, url, **kwargs):
            raise FetcherError("blocked", url=url, status=403)
        async def close(self): pass

    class OkSecondary(ApiFetcher):
        async def get(self, url, **kwargs):
            return FetcherResponse(url=url, body=b"{}", status=200)
        async def close(self): pass

    ff = FallbackFetcher(primary=FailingPrimary(), secondary=OkSecondary())
    try:
        resp = await ff.get("https://example.com")
    finally:
        await ff.close()
    assert resp.status == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fetcher_factory.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement FallbackFetcher**

Create `encar_parser/fetchers/factory.py`:

```python
"""Factory: combine a primary and a fallback fetcher."""

from __future__ import annotations

from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse


class FallbackFetcher:
    """Try primary first; on FetcherError with 403/429/timeout, use secondary.

    Other errors propagate (no fallback for 4xx, parse errors, etc.).
    """

    FALLBACK_STATUSES = {403, 429}

    def __init__(self, primary: Fetcher, secondary: Fetcher) -> None:
        self._primary = primary
        self._secondary = secondary

    async def get(self, url: str, **kwargs) -> FetcherResponse:
        try:
            return await self._primary.get(url, **kwargs)
        except FetcherError as e:
            if e.status in self.FALLBACK_STATUSES or e.status is None:
                return await self._secondary.get(url, **kwargs)
            raise

    async def close(self) -> None:
        await self._primary.close()
        await self._secondary.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fetcher_factory.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/fetchers/factory.py tests/unit/test_fetcher_factory.py
git commit -m "feat: FallbackFetcher with primary/secondary strategy"
```

---

## Task 12: Parsers (list_page + details)

**Files:**
- Create: `encar_parser/parsers/list_page.py`
- Create: `encar_parser/parsers/details.py`
- Create: `encar_parser/parsers/__init__.py`
- Create: `tests/unit/test_parsers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_parsers.py`:

```python
import pytest
import json
from datetime import date

from encar_parser.parsers.list_page import parse_search_list, SearchListItem
from encar_parser.parsers.details import parse_car_detail


# ---- list parser ----

def test_parse_search_list_extracts_ids():
    payload = {
        "SearchResults": {
            "EncarSearchResults": [
                {"Id": 42131435, "Manufacturer": "BMW", "Model": "X5 (G05)"},
                {"Id": 42131436, "Manufacturer": "BMW", "Model": "X5 (G05)"},
            ]
        }
    }
    items = parse_search_list(payload)
    assert len(items) == 2
    assert items[0].encar_id == 42131435
    assert items[0].brand == "BMW"
    assert items[0].model == "X5 (G05)"


def test_parse_search_list_handles_empty():
    payload = {"SearchResults": {"EncarSearchResults": []}}
    assert parse_search_list(payload) == []


def test_parse_search_list_handles_missing_key():
    """If structure differs, return empty list rather than crash."""
    assert parse_search_list({}) == []


# ---- details parser ----

def test_parse_car_detail_full():
    payload = {
        "car": {
            "vehicleNo": "158바6820",
            "year": "2025-11",
            "mileage": "4,027",
            "displacement": "2998",
            "fuel": {"name": "가솔린"},
            "transmission": {"name": "오토"},
            "bodyType": "SUV",
            "color": {"name": "검정색"},
            "seats": "5",
            "importType": {"name": "정식수입"},
            "manufacturerWarranty": "BMW",
            "liens": "0건",
            "seizures": "0건",
            "accidentRecords": 376,
            "price": "128500000",
            "photos": [
                "https://img.encar.com/car1/42131435_001.jpg",
                "https://img.encar.com/car1/42131435_002.jpg",
            ],
        }
    }
    car = parse_car_detail(encar_id=42131435, payload=payload)
    assert car.encar_id == 42131435
    assert car.brand == "BMW"  # passed in
    assert car.year_month == date(2025, 11, 1)
    assert car.mileage_km == 4027
    assert car.displacement_cc == 2998
    assert car.fuel_ru == "Бензин"
    assert car.fuel_original == "가솔린"
    assert car.transmission_ru == "Автомат"
    assert car.body_type == "SUV"
    assert car.color_ru == "Чёрный"
    assert car.seats == 5
    assert car.import_type_ru == "Официальный"
    assert car.liens_seizures == "0건·0건"
    assert car.accident_records == 376
    assert car.price_krw == 128500000
    assert len(car.photo_urls) == 2
    assert car.encar_detail_url == "https://fem.encar.com/cars/detail/42131435"


def test_parse_car_detail_handles_missing_optional_fields():
    payload = {"car": {"year": "2020-01"}}
    car = parse_car_detail(encar_id=1, payload=payload, brand="Kia", model="Rio")
    assert car.encar_id == 1
    assert car.brand == "Kia"
    assert car.year_month == date(2020, 1, 1)
    assert car.mileage_km is None
    assert car.fuel_ru is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_parsers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement parsers**

Create `encar_parser/parsers/__init__.py`:

```python
from encar_parser.parsers.list_page import SearchListItem, parse_search_list
from encar_parser.parsers.details import CarData, parse_car_detail

__all__ = ["SearchListItem", "parse_search_list", "CarData", "parse_car_detail"]
```

Create `encar_parser/parsers/list_page.py`:

```python
"""Parse the JSON list response from encar search API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SearchListItem:
    """A minimal representation of a car in the search results."""

    encar_id: int
    brand: str
    model: str


def parse_search_list(payload: Any) -> list[SearchListItem]:
    """Extract list of (encar_id, brand, model) from the search API JSON.

    Defensive: returns [] on missing keys or unexpected structure.
    """
    try:
        results = payload["SearchResults"]["EncarSearchResults"]
    except (KeyError, TypeError):
        return []

    items: list[SearchListItem] = []
    for entry in results:
        try:
            items.append(
                SearchListItem(
                    encar_id=int(entry["Id"]),
                    brand=str(entry.get("Manufacturer", "")),
                    model=str(entry.get("Model", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed entries
    return items
```

Create `encar_parser/parsers/details.py`:

```python
"""Parse the JSON car detail response from encar."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from encar_parser.translations import (
    translate_color,
    translate_fuel,
    translate_import_type,
    translate_transmission,
)


@dataclass
class CarData:
    """Parsed car data ready to be inserted/updated in the DB."""

    encar_id: int
    brand: str
    model: str
    year_month: date | None = None
    mileage_km: int | None = None
    displacement_cc: int | None = None
    fuel_ru: str | None = None
    fuel_original: str | None = None
    transmission_ru: str | None = None
    transmission_orig: str | None = None
    body_type: str | None = None
    color_ru: str | None = None
    color_original: str | None = None
    seats: int | None = None
    import_type_ru: str | None = None
    manufacturer_warranty: str | None = None
    liens_seizures: str | None = None
    accident_records: int | None = None
    plate_number: str | None = None
    price_krw: int | None = None
    photo_urls: list[str] = field(default_factory=list)
    encar_detail_url: str = ""
    raw_data: dict[str, Any] | None = None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def _parse_year_month(value: Any) -> date | None:
    """Parse KR year like '25년 11월' or ISO '2025-11' to a date."""
    if not value:
        return None
    s = str(value)
    # ISO 2025-11 or 2025.11
    m = re.match(r"(\d{4})[-.](\d{1,2})", s)
    if m:
        return date(int(m.group(1)), min(int(m.group(2)), 12), 1)
    # KR 25년 11월
    m = re.search(r"(\d{2})년\s*(\d{1,2})월", s)
    if m:
        year_2digit = int(m.group(1))
        year = 2000 + year_2digit if year_2digit < 50 else 1900 + year_2digit
        return date(year, min(int(m.group(2)), 12), 1)
    return None


def _nested(d: dict, *keys: str, default: Any = None) -> Any:
    """Look up a nested dict, returning default if any key is missing."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def parse_car_detail(
    *,
    encar_id: int,
    payload: Any,
    brand: str = "",
    model: str = "",
) -> CarData:
    """Parse the JSON car detail response from encar.

    Brand and model can be passed in (e.g. from the list parser) as fallbacks.
    """
    car = _nested(payload, "car", default={}) or {}
    if not isinstance(car, dict):
        car = {}

    fuel_orig = _nested(car, "fuel", "name")
    trans_orig = _nested(car, "transmission", "name")
    color_orig = _nested(car, "color", "name")
    import_orig = _nested(car, "importType", "name")

    liens = _nested(car, "liens", default="")
    seizures = _nested(car, "seizures", default="")
    liens_seizures: str | None = None
    if liens or seizures:
        liens_seizures = f"{liens or '0건'}·{seizures or '0건'}"

    photos = car.get("photos") or []
    if not isinstance(photos, list):
        photos = []

    return CarData(
        encar_id=encar_id,
        brand=brand or car.get("manufacturer", ""),
        model=model or car.get("model", ""),
        year_month=_parse_year_month(car.get("year") or car.get("modelYear")),
        mileage_km=_to_int(car.get("mileage")),
        displacement_cc=_to_int(car.get("displacement")),
        fuel_ru=translate_fuel(fuel_orig) if fuel_orig else None,
        fuel_original=fuel_orig,
        transmission_ru=translate_transmission(trans_orig) if trans_orig else None,
        transmission_orig=trans_orig,
        body_type=car.get("bodyType"),
        color_ru=translate_color(color_orig) if color_orig else None,
        color_original=color_orig,
        seats=_to_int(car.get("seats")),
        import_type_ru=translate_import_type(import_orig) if import_orig else None,
        manufacturer_warranty=car.get("manufacturerWarranty"),
        liens_seizures=liens_seizures,
        accident_records=_to_int(car.get("accidentRecords")),
        plate_number=car.get("vehicleNo"),
        price_krw=_to_int(car.get("price")),
        photo_urls=[str(p) for p in photos if isinstance(p, str)],
        encar_detail_url=f"https://fem.encar.com/cars/detail/{encar_id}",
        raw_data=car if isinstance(car, dict) else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_parsers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/parsers/ tests/unit/test_parsers.py
git commit -m "feat: list and details parsers with translation"
```

---

## Task 13: Scheduler (3-day rotation)

**Files:**
- Create: `encar_parser/scheduler.py`
- Create: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_scheduler.py`:

```python
from datetime import date

import pytest

from encar_parser.encar_url import ModelConfig
from encar_parser.db.models import SearchModel
from encar_parser.scheduler import models_for_today


def _mk(slug: str, priority: int = 100) -> SearchModel:
    cfg = ModelConfig(slug=slug, name=slug, priority=priority)
    return SearchModel(
        slug=slug, name=slug, encar_url="", encar_action={}, priority=priority
    )


def test_models_for_today_divides_by_three_days():
    models = [_mk(f"m{i:02d}") for i in range(6)]
    # 2026-06-15 is a Monday (isoweekday=1, bucket=0)
    # bucket = (isoweekday - 1) % 3
    day1 = date(2026, 6, 15)  # Monday, bucket 0
    day2 = date(2026, 6, 16)  # Tuesday, bucket 1
    day3 = date(2026, 6, 17)  # Wednesday, bucket 2

    d1 = [m.slug for m in models_for_today(models, day1)]
    d2 = [m.slug for m in models_for_today(models, day2)]
    d3 = [m.slug for m in models_for_today(models, day3)]

    # Each model appears in exactly one bucket; coverage is full
    assert set(d1) | set(d2) | set(d3) == {m.slug for m in models}
    assert len(d1 & d2) == 0
    assert len(d2 & d3) == 0
    assert len(d1 & d3) == 0


def test_models_for_today_deterministic():
    models = [_mk(f"m{i:02d}") for i in range(9)]
    day = date(2026, 6, 15)
    result1 = [m.slug for m in models_for_today(models, day)]
    result2 = [m.slug for m in models_for_today(models, day)]
    assert result1 == result2


def test_models_for_today_respects_priority():
    models = [_mk("z_high", priority=10), _mk("a_low", priority=99)]
    day = date(2026, 6, 15)  # bucket 0
    result = [m.slug for m in models_for_today(models, day)]
    # z_high has priority 10, a_low 99, so z_high comes first within bucket
    # bucket 0 contains even-indexed models after sort
    # after sort by (priority, slug): z_high (10), a_low (99)
    # index 0 -> bucket 0, index 1 -> bucket 1
    assert result == ["z_high"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement scheduler**

Create `encar_parser/scheduler.py`:

```python
"""Decide which models to process today based on a 3-day rotation."""

from __future__ import annotations

from datetime import date

from encar_parser.db.models import SearchModel


def models_for_today(
    models: list[SearchModel], today: date
) -> list[SearchModel]:
    """Return the subset of enabled models assigned to today.

    Models are sorted by (priority, slug) for determinism. Then split into
    3 buckets based on their sorted index. The bucket for `today` is
    `(today.isoweekday() - 1) % 3`.
    """
    sorted_models = sorted(models, key=lambda m: (m.priority, m.slug))
    bucket = (today.isoweekday() - 1) % 3
    return [m for i, m in enumerate(sorted_models) if i % 3 == bucket]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat: 3-day rotation scheduler"
```

---

## Task 14: Rate limiter

**Files:**
- Create: `encar_parser/utils/rate_limit.py`
- Create: `tests/unit/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rate_limit.py`:

```python
import asyncio
import time

import pytest

from encar_parser.utils.rate_limit import TokenBucket, RandomDelay


@pytest.mark.asyncio
async def test_token_bucket_consumes_and_refills():
    bucket = TokenBucket(capacity=2, refill_per_sec=10)
    assert await bucket.acquire() is True
    assert await bucket.acquire() is True
    # 3rd should wait a bit
    start = time.monotonic()
    assert await bucket.acquire() is True
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # had to wait for refill


@pytest.mark.asyncio
async def test_random_delay_within_range():
    rd = RandomDelay(min_sec=0.05, max_sec=0.1)
    start = time.monotonic()
    await rd.wait()
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed <= 0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rate_limit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement rate limiter**

Create `encar_parser/utils/rate_limit.py`:

```python
"""Async rate-limiting primitives."""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    """A simple token-bucket rate limiter.

    `capacity` is the maximum number of tokens (max burst).
    `refill_per_sec` is the steady-state rate at which tokens refill.
    """

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self._capacity = capacity
        self._refill_per_sec = refill_per_sec
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._refill_per_sec
            )
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            # Wait until next token
            wait_sec = (1 - self._tokens) / self._refill_per_sec
        await asyncio.sleep(wait_sec)
        async with self._lock:
            self._tokens -= 1
            return True


class RandomDelay:
    """Sleeps for a random duration between min_sec and max_sec."""

    def __init__(self, min_sec: float, max_sec: float) -> None:
        self._min = min_sec
        self._max = max_sec

    async def wait(self) -> None:
        await asyncio.sleep(random.uniform(self._min, self._max))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rate_limit.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/utils/rate_limit.py tests/unit/test_rate_limit.py
git commit -m "feat: token bucket and random delay"
```

---

## Task 15: Pipeline (main loop)

**Files:**
- Create: `encar_parser/pipeline.py`
- Create: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Write the integration test (using respx + sqlite)**

Create `tests/integration/__init__.py` (empty file).

Create `tests/integration/test_pipeline.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, SearchModel
from encar_parser.encar_url import ModelConfig
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherResponse
from encar_parser.pipeline import run_model


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _list_payload() -> dict:
    return {
        "SearchResults": {
            "EncarSearchResults": [
                {"Id": 42131435, "Manufacturer": "BMW", "Model": "X5 (G05)"},
            ]
        }
    }


def _detail_payload() -> dict:
    return {
        "car": {
            "vehicleNo": "158바6820",
            "year": "2025-11",
            "mileage": "4,027",
            "displacement": "2998",
            "fuel": {"name": "가솔린"},
            "transmission": {"name": "오토"},
            "bodyType": "SUV",
            "color": {"name": "검정색"},
            "seats": "5",
            "importType": {"name": "정식수입"},
            "manufacturerWarranty": "BMW",
            "liens": "0건",
            "seizures": "0건",
            "accidentRecords": 0,
            "price": "128500000",
            "photos": ["https://img.encar.com/x.jpg"],
        }
    }


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
@respx.mock
async def test_run_model_inserts_car_and_link(session):
    sm = SearchModel(
        id=1, slug="bmw-x5-g05", name="BMW X5 (G05)",
        encar_url="https://example.com", encar_action={}
    )
    session.add(sm)
    await session.commit()

    # First call returns the list, second call returns the detail
    respx.get("https://api.encar.com/search/list").mock(
        return_value=httpx.Response(200, json=_list_payload())
    )
    respx.get("https://fem.encar.com/cars/detail/42131435").mock(
        return_value=httpx.Response(200, json=_detail_payload())
    )

    fetcher = ApiFetcher()
    await fetcher.__aenter__()
    try:
        from encar_parser.encar_url import build_url
        cfg = ModelConfig(
            slug=sm.slug, name=sm.name,
            manufacturer="BMW", model_group="X5", model="X5 (G05)"
        )
        cars_count = await run_model(
            sm, fetcher=fetcher, session=session,
            list_url=build_url(cfg),
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    assert cars_count == 1
    cars = (await session.execute(select(Car))).scalars().all()
    assert len(cars) == 1
    matches = (await session.execute(select(CarModelMatch))).scalars().all()
    assert len(matches) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement pipeline**

Create `encar_parser/pipeline.py`:

```python
"""Main pipeline: run a single model end-to-end."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from encar_parser.db.models import SearchModel
from encar_parser.db.repository import link_car_to_model, upsert_car
from encar_parser.fetchers.base import Fetcher
from encar_parser.parsers.details import parse_car_detail
from encar_parser.parsers.list_page import parse_search_list
from encar_parser.utils.log import get_logger
from encar_parser.utils.rate_limit import RandomDelay

log = get_logger(__name__)


def _decode_hash_from_url(url: str) -> dict[str, Any]:
    """Extract the JSON action dict from an encar search URL hash."""
    if "#!" not in url:
        return {}
    _, encoded = url.split("#!", 1)
    decoded = urllib.parse.unquote(encoded)
    return json.loads(decoded)


async def _fetch_list_with_meta(fetcher: Fetcher, url: str) -> list[Any]:
    """Fetch the search list and return raw SearchListItem objects (with brand/model)."""
    resp = await fetcher.get(url)
    payload = resp.json()
    return parse_search_list(payload)


async def _fetch_one_car(
    fetcher: Fetcher,
    encar_id: int,
    brand: str,
    model: str,
    detail_url_template: str,
) -> Any:
    """Fetch one car detail and return a CarData object."""
    url = detail_url_template.format(encar_id=encar_id)
    resp = await fetcher.get(url)
    payload = resp.json()
    return parse_car_detail(encar_id=encar_id, payload=payload, brand=brand, model=model)


async def run_model(
    search_model: SearchModel,
    *,
    fetcher: Fetcher,
    session: AsyncSession,
    list_url: str,
    detail_url_template: str,
    request_delay: RandomDelay | None = None,
) -> int:
    """Process one model: list all cars, fetch each, upsert into DB.

    Returns the number of cars successfully inserted/updated.
    """
    request_delay = request_delay or RandomDelay(0.01, 0.05)  # fast in tests
    fetched = 0

    log.info("model_start", slug=search_model.slug, name=search_model.name)

    try:
        items = await _fetch_list_with_meta(fetcher, list_url)
    except Exception as e:
        log.error("model_list_failed", slug=search_model.slug, error=str(e))
        raise

    log.info("model_list_ok", slug=search_model.slug, count=len(items))

    for item in items:
        try:
            await request_delay.wait()
            car_data = await _fetch_one_car(
                fetcher, item.encar_id, item.brand, item.model, detail_url_template
            )
            await upsert_car(
                session,
                encar_id=car_data.encar_id,
            await upsert_car(
                session,
                encar_id=car_data.encar_id,
                brand=car_data.brand,
                model=car_data.model,
                year_month=car_data.year_month,
                mileage_km=car_data.mileage_km,
                displacement_cc=car_data.displacement_cc,
                fuel_ru=car_data.fuel_ru,
                fuel_original=car_data.fuel_original,
                transmission_ru=car_data.transmission_ru,
                transmission_orig=car_data.transmission_orig,
                body_type=car_data.body_type,
                color_ru=car_data.color_ru,
                color_original=car_data.color_original,
                seats=car_data.seats,
                import_type_ru=car_data.import_type_ru,
                manufacturer_warranty=car_data.manufacturer_warranty,
                liens_seizures=car_data.liens_seizures,
                accident_records=car_data.accident_records,
                plate_number=car_data.plate_number,
                price_krw=car_data.price_krw,
                photo_urls=car_data.photo_urls,
                encar_detail_url=car_data.encar_detail_url,
                raw_data=car_data.raw_data,
            )
            await link_car_to_model(
                session, search_model_id=search_model.id, encar_id=item.encar_id
            )
            fetched += 1
            log.info("car_fetched", slug=search_model.slug, encar_id=item.encar_id)
        except Exception as e:
            log.error(
                "car_failed",
                slug=search_model.slug,
                encar_id=item.encar_id,
                error=str(e),
            )
            continue

    search_model.last_run_at = datetime.now(timezone.utc)
    await session.commit()
    log.info("model_done", slug=search_model.slug, fetched=fetched)
    return fetched
```

- [ ] **Step 4: Run integration test to verify it passes**

Run: `uv run pytest tests/integration/test_pipeline.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add encar_parser/pipeline.py tests/integration/test_pipeline.py tests/integration/__init__.py
git commit -m "feat: pipeline.run_model for end-to-end model processing"
```

---

## Task 16: CLI (Typer)

**Files:**
- Create: `encar_parser/cli.py`
- Create: `encar_parser/__main__.py`

- [ ] **Step 1: Implement CLI**

Create `encar_parser/cli.py`:

```python
"""Typer CLI for the encar parser."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
import yaml
from sqlalchemy import select

from encar_parser.config import get_settings
from encar_parser.db.models import Run, SearchModel
from encar_parser.db.repository import (
    get_enabled_models,
    upsert_search_model,
)
from encar_parser.db.session import get_sessionmaker
from encar_parser.encar_url import build_url
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.browser import BrowserFetcher
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.pipeline import run_model
from encar_parser.scheduler import models_for_today
from encar_parser.utils.log import get_logger, setup_logging
from encar_parser.utils.rate_limit import RandomDelay

log = get_logger(__name__)
app = typer.Typer(help="Encar parser CLI")


def _load_models_yaml(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        typer.echo(f"models.yaml not found at {path}", err=True)
        raise typer.Exit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("models", [])


@app.command()
def sync(
    config_path: Path = typer.Option(Path("models.yaml"), "--config", "-c"),
) -> None:
    """Synchronize models.yaml into the database."""
    setup_logging()
    asyncio.run(_sync_async(config_path))


async def _sync_async(config_path: Path) -> None:
    items = _load_models_yaml(config_path)
    Session = get_sessionmaker()
    async with Session() as session:
        seen_slugs: set[str] = set()
        for item in items:
            cfg = build_url_from_item(item)
            await upsert_search_model(
                session,
                slug=cfg["slug"],
                name=cfg["name"],
                encar_url=cfg["encar_url"],
                encar_action=cfg["encar_action"],
                enabled=cfg.get("enabled", True),
                priority=cfg.get("priority", 100),
            )
            seen_slugs.add(cfg["slug"])
            log.info("model_synced", slug=cfg["slug"])

        # Disable models no longer in YAML
        result = await session.scalars(select(SearchModel))
        for sm in result.all():
            if sm.slug not in seen_slugs:
                sm.enabled = False
                log.info("model_disabled", slug=sm.slug)
        await session.commit()
    typer.echo(f"Synced {len(items)} models.")


def build_url_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a YAML model item to the fields needed for upsert."""
    from encar_parser.encar_url import ModelConfig, build_action, build_url as _build_url
    cfg = ModelConfig(**{k: v for k, v in item.items() if k != "enabled"})
    return {
        "slug": item["slug"],
        "name": item["name"],
        "encar_url": _build_url(cfg),
        "encar_action": build_action(cfg),
        "enabled": item.get("enabled", True),
        "priority": item.get("priority", 100),
    }


@app.command()
def run() -> None:
    """Run today's scheduled models."""
    setup_logging()
    asyncio.run(_run_async())


async def _run_async() -> None:
    settings = get_settings()
    Session = get_sessionmaker()
    async with Session() as session:
        all_models = await get_enabled_models(session)
        today_models = models_for_today(all_models, datetime.now(timezone.utc).date())
        if not today_models:
            typer.echo("No models scheduled for today.")
            return

        run_record = Run(
            started_at=datetime.now(timezone.utc),
            models_planned=len(today_models),
            models_done=0,
            cars_fetched=0,
            cars_failed=0,
            error_log=[],
        )
        session.add(run_record)
        await session.commit()
        await session.refresh(run_record)

        async with ApiFetcher() as api, BrowserFetcher() as browser:
            fetcher = FallbackFetcher(primary=api, secondary=browser)
            request_delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)
            for sm in today_models:
                try:
                    count = await run_model(
                        sm,
                        fetcher=fetcher,
                        session=session,
                        list_url=sm.encar_url,
                        detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
                        request_delay=request_delay,
                    )
                    run_record.models_done += 1
                    run_record.cars_fetched += count
                except Exception as e:
                    run_record.cars_failed += 1
                    log.error("model_failed", slug=sm.slug, error=str(e))
                    if run_record.error_log is None:
                        run_record.error_log = []
                    run_record.error_log.append({"slug": sm.slug, "error": str(e)})
                # Pause between models
                await asyncio.sleep(random.uniform(
                    settings.min_model_delay_sec, settings.max_model_delay_sec
                ))

        run_record.finished_at = datetime.now(timezone.utc)
        await session.commit()

        typer.echo(json.dumps({
            "run_id": run_record.id,
            "models_planned": run_record.models_planned,
            "models_done": run_record.models_done,
            "cars_fetched": run_record.cars_fetched,
            "cars_failed": run_record.cars_failed,
        }, ensure_ascii=False, indent=2))


@app.command()
def migrate() -> None:
    """Run alembic migrations."""
    import subprocess
    setup_logging()
    result = subprocess.run(["alembic", "upgrade", "head"], check=False)
    raise typer.Exit(result.returncode)
```

Create `encar_parser/__main__.py`:

```python
"""Entrypoint: `python -m encar_parser`."""

from encar_parser.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Verify CLI works**

Run:
```bash
uv run python -m encar_parser --help
```

Expected: Shows the help with `sync`, `run`, `migrate` subcommands.

- [ ] **Step 3: Commit**

```bash
git add encar_parser/cli.py encar_parser/__main__.py
git commit -m "feat: Typer CLI with sync, run, migrate commands"
```

---

## Task 17: Dockerfile & docker-compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `docker/cron/Dockerfile`
- Create: `docker/cron/entrypoint.sh`
- Create: `docker/cron/crontab`

- [ ] **Step 1: Create main Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY encar_parser ./encar_parser
COPY alembic ./alembic
COPY alembic.ini ./
COPY models.yaml ./

# Stage 2: cron runner
FROM base AS cron
COPY docker/cron/entrypoint.sh /entrypoint.sh
COPY docker/cron/crontab /etc/cron.d/encar
RUN chmod 0644 /etc/cron.d/encar && crontab /etc/cron.d/encar
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
```

- [ ] **Step 2: Create entrypoint**

Create `docker/cron/entrypoint.sh`:

```bash
#!/bin/sh
set -e

echo "[entrypoint] Running migrations..."
uv run --no-sync alembic upgrade head

echo "[entrypoint] Syncing models..."
uv run --no-sync python -m encar_parser sync

echo "[entrypoint] Starting cron..."
exec cron -f
```

- [ ] **Step 3: Create crontab**

Create `docker/cron/crontab`:

```
# Run encar parser daily at 03:00 (Korea time, UTC+9 → 18:00 UTC)
# Adjust timezone of the host if needed
0 3 * * * cd /app && uv run --no-sync python -m encar_parser run >> /var/log/encar.log 2>&1
```

- [ ] **Step 4: Create docker-compose.yml**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: encar
      POSTGRES_USER: encar
      POSTGRES_PASSWORD: ${DB_PASSWORD:-encar}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U encar -d encar"]
      interval: 5s
      timeout: 5s
      retries: 5

  parser:
    build:
      context: .
      target: cron
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://encar:${DB_PASSWORD:-encar}@postgres:5432/encar
    volumes:
      - ./models.yaml:/app/models.yaml:ro
      - encar_logs:/var/log

volumes:
  pgdata:
  encar_logs:
```

- [ ] **Step 5: Verify build works (will fail on DB but the image should build)**

Run:
```bash
docker compose build parser
```

Expected: Build succeeds (DB connection failure at runtime is OK).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml docker/
git commit -m "feat: Docker setup with cron-based scheduler"
```

---

## Task 18: E2E smoke test (live, opt-in)

**Files:**
- Create: `tests/e2e/test_smoke_live.py`

- [ ] **Step 1: Write the live smoke test**

Create `tests/e2e/__init__.py` (empty file).

Create `tests/e2e/test_smoke_live.py`:

```python
"""End-to-end test that hits the real encar.com. Marked as @pytest.mark.live.

Run with: uv run pytest tests/e2e -m live
Skip by default in CI.
"""

import pytest

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.parsers.list_page import parse_search_list


@pytest.mark.asyncio
@pytest.mark.live
async def test_smoke_fetch_first_page_bmw_x5():
    """Fetch the first page of BMW X5 listings and verify the parser works."""
    from encar_parser.encar_url import ModelConfig, build_url

    cfg = ModelConfig(
        slug="bmw-x5-g05",
        name="BMW X5 (G05)",
        manufacturer="BMW",
        model_group="X5",
        model="X5 (G05)",
        year_from=2018,
    )
    url = build_url(cfg)

    async with ApiFetcher() as f:
        resp = await f.get(url)
        assert resp.status == 200
        items = parse_search_list(resp.json())
        assert len(items) > 0, "Expected at least one car on first page"
        assert items[0].encar_id > 0
```

- [ ] **Step 2: Verify it is collected but skipped**

Run:
```bash
uv run pytest tests/e2e/ -v --collect-only
```

Expected: 1 test collected, marked as skipped (no `live` marker).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/
git commit -m "test: e2e smoke test marked @pytest.mark.live"
```

---

## Task 19: Final verification

- [ ] **Step 1: Run all unit + integration tests**

Run: `uv run pytest tests/unit tests/integration -v`
Expected: All tests pass.

- [ ] **Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors.

- [ ] **Step 3: Run type checker**

Run: `uv run mypy encar_parser`
Expected: No errors (or only known ignorable warnings).

- [ ] **Step 4: Verify the design matches the spec**

Walk through the spec checklist:

- [x] Architecture (Task 1, 8, 11)
- [x] DB schema (Task 5, 6, 7)
- [x] 18 fields parsed (Task 12)
- [x] Translation rules (Task 3, 12)
- [x] YAML config (Task 4)
- [x] Scheduler (Task 13)
- [x] Rate limit / ban protection (Task 14, 9)
- [x] Error handling / retry (Task 9, 10, 11)
- [x] Pipeline (Task 15)
- [x] CLI (Task 16)
- [x] Docker (Task 17)
- [x] Tests (Tasks 1-18)

- [ ] **Step 5: Manual smoke against real encar.com**

Run (with internet and a working .env):
```bash
uv run python -m encar_parser sync
uv run pytest tests/e2e -m live -v
```

Expected: 1 model synced, smoke test passes.

- [ ] **Step 6: Final commit**

```bash
git add .
git commit --allow-empty -m "chore: MVP ready for review"
```

---

## Self-Review

**Spec coverage:** All 12 sections of the spec are covered by tasks 1-19.

**Placeholder scan:** No "TBD"/"TODO" in task steps. All code blocks are complete.

**Type consistency:**
- `Fetcher.get(url, *, params=None)` is consistent across `base.py`, `api.py`, `browser.py`, `factory.py`
- `FetcherResponse(url, body, status, headers)` consistent
- `parse_car_detail(encar_id, payload, brand, model)` signature consistent with `pipeline._fetch_one_car`
- `upsert_car(session, *, encar_id, brand, model, **fields)` consistent between repository and pipeline
- `ModelConfig(slug, name, ...)` consistent between YAML loading, encar_url, and repository

**Potential issues to flag for the implementer:**

1. The exact encar API endpoint is unknown — Task 15 uses `https://api.encar.com/search/list` and `https://fem.encar.com/cars/detail/{id}` as placeholders. The hired specialist should verify these by:
   - Opening browser devtools on `https://www.encar.com/fc/fc_carsearchlist.do?carType=for`
   - Watching the Network tab for the actual JSON endpoint
   - Updating `pipeline.py` and `ApiFetcher` with the real URLs

2. The `car` JSON structure in `tests/integration/test_pipeline.py` is a guess. The specialist should record a real response and adjust `parsers/details.py` to match the actual field names (the test fixture in `tests/fixtures/car_detail_42131435.json` will reveal them).

3. The `accidentRecords=0` field in the integration test — the spec notes that the real value (376) seems suspicious. The specialist should confirm what this field actually represents.

**Open questions from spec (p. 11):** These should be answered during the first real fetch, but do not block implementation. They're listed in the spec for the specialist to confirm.
