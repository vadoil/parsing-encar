# PROJECT_REPORT — encar-parser

> Сгенерировано 2026-06-18 на основе полного сканирования репозитория `/Users/mac/ClaudeProjects/parsing encar`. Только чтение, никаких изменений в коде.

---

## 1. Что это за проект

`encar-parser` — асинхронный Python-парсер корейского маркетплейса подержанных автомобилей **encar.com** (главный публичный сайт `https://www.encar.com`, внутренний API — `https://api.encar.com`).

**Цель:** собрать каталог авто из 100+ сохранённых поисковых фильтров, перевести корейские поля на русский и подготовить данные для последующей публикации на собственном сайте-каталоге.

**Формат выгрузки:** 18 полей на авто (encar_id, brand, model, year_month, mileage_km, displacement_cc, fuel, transmission, body, color, seats, import_type, manufacturer_warranty, liens_seizures, accident_records, plate_number, price_krw, photo_urls, encar_detail_url) + ссылка на оригинальную карточку.

**Особенности:** гибридный fetcher — `httpx` как основной клиент, headless Chromium (Playwright) как fallback при 403/429. Защита от бана без прокси: ротация User-Agent, задержки, `Accept-Language: ko-KR`, корректный `Referer`. 3-дневная ротация моделей (каждый день парсится только треть).

**Состояние:** MVP завершён (19 задач плана от 2026-06-15). Тесты: 59 unit+integration pass, 2 live-маркированы и пропускаются по умолчанию. В рабочей копии висят 10 незакоммиченных файлов после live-фикса от 2026-06-17.

---

## 2. Стек и версии

**Язык:** Python ≥ 3.11 (используется синтаксис `X | Y`, `list[...]`, `dict[...]`).
**Пакетный менеджер:** `uv` (зависимости и lock-файл — `pyproject.toml` + `uv.lock`).
**Никаких `requirements.txt`, `Pipfile`, `package.json`** — проект чисто Python.

**Зависимости runtime (из `pyproject.toml`):**
| Пакет | Минимальная версия | Назначение |
|---|---|---|
| `httpx` | 0.27 | async HTTP-клиент (основной fetcher) |
| `playwright` | 1.47 | headless Chromium (fallback) |
| `sqlalchemy[asyncio]` | 2.0 | async ORM |
| `asyncpg` | 0.29 | async-драйвер PostgreSQL |
| `alembic` | 1.13 | миграции БД |
| `pydantic` | 2.7 | модели конфигов |
| `pydantic-settings` | 2.3 | чтение `.env` |
| `structlog` | 24.1 | JSON-логирование |
| `tenacity` | 9.0 | ретраи |
| `typer` | 0.12 | CLI |
| `pyyaml` | 6.0 | парсинг `models.yaml` |
| `python-dotenv` | 1.0 | загрузка `.env` |

**Зависимости dev:** `pytest ≥ 8.2`, `pytest-asyncio ≥ 0.23`, `pytest-cov ≥ 5.0`, `respx ≥ 0.21` (мок httpx), `ruff ≥ 0.5`, `mypy ≥ 1.10`, `aiosqlite ≥ 0.22.1` (in-memory БД в тестах).

**Инфраструктура:**
- PostgreSQL 16 (alpine-образ в `docker-compose.yml`)
- Docker + Docker Compose v2
- cron внутри контейнера (не host-cron)
- Python 3.11-slim базовый образ

**Линтинг/типизация:** `ruff` (line-length=100, `py311`, включает E/F/I/N/W/UP/B/A/C4/PT/RET/SIM), `mypy`, `shellcheck` (для bash-скриптов deploy/).

---

## 3. Дерево файлов проекта

Исключены: `.git/`, `.venv/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `output/_raw_*.json`, `output/_details.json`, `output/encar_export.*` (генерируются).

```
.
├── .env.example                    # шаблон переменных окружения (dev)
├── .gitignore
├── alembic.ini                     # конфиг Alembic (script_location, формат логов)
├── alembic/
│   ├── env.py                      # async-обёртка над Alembic (run_async_migrations)
│   └── versions/
│       └── 0001_initial_schema.py  # первая миграция: 4 таблицы + индексы
├── CHANGES_RU.md                   # человекочитаемый changelog (последний фикс API)
├── docker-compose.yml              # 2 сервиса: postgres + parser
├── docker/
│   └── cron/
│       ├── crontab                 # 0 3 * * * python -m encar_parser run
│       └── entrypoint.sh           # alembic upgrade + sync + cron -f
├── Dockerfile                      # multi-stage: base (uv sync) → cron (crontab)
├── docs/
│   └── superpowers/
│       ├── plans/
│       │   ├── 2026-06-15-encar-parser.md       # 19 задач MVP (завершены)
│       │   └── 2026-06-18-vps-deployment.md     # 10 задач деплоя (НЕ начат)
│       └── specs/
│           └── 2026-06-15-encar-parser-design.md # дизайн-спека
├── encar_parser/
│   ├── __init__.py                 # пустой
│   ├── __main__.py                 # entrypoint: python -m encar_parser
│   ├── cli.py                      # Typer: sync, run, migrate, probe
│   ├── config.py                   # pydantic Settings, читает .env
│   ├── encar_url.py                # YAML → S-expression → URL encar API
│   ├── pipeline.py                 # run_model(): основной цикл для одной модели
│   ├── scheduler.py                # 3-дневная ротация
│   ├── translations.py             # KO → RU словари
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py               # SearchModel, Car, CarModelMatch, Run
│   │   ├── repository.py           # upsert_search_model, upsert_car, link_car_to_model
│   │   └── session.py              # async_engine, sessionmaker
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py                 # Protocol Fetcher, FetcherResponse, FetcherError
│   │   ├── api.py                  # ApiFetcher (httpx, ротация UA)
│   │   ├── browser.py              # BrowserFetcher (Playwright, fallback)
│   │   └── factory.py              # FallbackFetcher (primary → secondary при 403/429)
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── list_page.py            # SearchResults[] + Count → list[SearchListItem]
│   │   └── details.py              # JSON карточки → CarData (legacy + real shape)
│   └── utils/
│       ├── __init__.py
│       ├── log.py                  # structlog JSON setup
│       ├── rate_limit.py           # TokenBucket + RandomDelay
│       └── ua.py                   # 10 User-Agent строк (Chrome/Safari/Edge/Firefox)
├── Makefile                        # install, test, test-live, lint, format, migrate, run, sync
├── models.yaml                     # конфиг поисковых фильтров (3 модели, в т.ч. BMW X5 G05)
├── output/
│   ├── build_export.py             # CSV + HTML + JSON-LD экспорт из кеша
│   ├── download_photos.py          # зеркало ci.encar.com в output/photos/{carid}/
│   ├── encar_export.csv            # ← ГЕНЕРИРУЕТСЯ, в .gitignore
│   ├── encar_export.html           # ← ГЕНЕРИРУЕТСЯ, в .gitignore
│   └── encar_export.jsonld.json    # ← ГЕНЕРИРУЕТСЯ, в .gitignore
├── pyproject.toml                  # имя, версия 0.1.0, deps, ruff, pytest
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/                       # 13 файлов, ~700 строк
│   ├── integration/                # test_pipeline.py
│   └── e2e/                        # test_smoke_live.py (маркер @pytest.mark.live)
├── uv.lock                         # детерминированный lock от uv
└── mockup.html                     # визуальный мокап карточки (референс для экспорта)
```

**Итого исходников:** 28 Python-файлов в `encar_parser/` (~1500 строк), 14 файлов тестов (~900 строк), 1 миграция, 2 bash-скрипта в `docker/`, 2 Python-скрипта в `output/`.

---

## 4. Назначение ключевых файлов

### `encar_parser/` (бизнес-логика)

| Файл | Строк | Назначение |
|---|---|---|
| `cli.py` | 226 | Typer-приложение: команды `sync` (YAML→DB), `run` (парсинг), `migrate` (alembic), `probe` (smoke-тест живого API) |
| `config.py` | 49 | Pydantic `Settings`: DATABASE_URL, API endpoints, rate limits, тайминги. Singleton через `get_settings()` |
| `encar_url.py` | 150 | **Ядро**: YAML-конфиг → S-expression `q` → URL encar API. `build_q()`, `build_sr()`, `build_list_api_url()` |
| `pipeline.py` | 128 | `run_model()` — главный цикл: list → per-car detail → upsert в БД → commit |
| `scheduler.py` | 21 | `models_for_today()` — делит отсортированные модели на 3 бакета по `(isoweekday()-1) % 3` |
| `translations.py` | 60 | KO→RU словари для fuel/transmission/color/import_type + 4 функции-translator |
| `parsers/list_page.py` | 78 | `parse_search_list()`, `parse_search_list_result()` — JSON списка → dataclass |
| `parsers/details.py` | 249 | `parse_car_detail()` — JSON карточки → `CarData`. Поддерживает legacy (`car.*`) и real API (flat) shape |

### `encar_parser/db/`

| Файл | Строк | Назначение |
|---|---|---|
| `models.py` | 128 | ORM: `SearchModel`, `Car`, `CarModelMatch`, `Run`. 3 индекса на `cars` |
| `repository.py` | 124 | `upsert_search_model()`, `upsert_car()`, `link_car_to_model()`, `get_enabled_models()` |
| `session.py` | 40 | `create_async_engine`, `async_sessionmaker`, `get_session()`. Pool 5+10, `pool_pre_ping` |

### `encar_parser/fetchers/`

| Файл | Строк | Назначение |
|---|---|---|
| `base.py` | 46 | `FetcherResponse` (body/status/headers + `.json()`), `FetcherError`, `Fetcher` Protocol |
| `api.py` | 85 | `ApiFetcher` — async context manager, `httpx.AsyncClient`, ротация UA, 403/429 → FetcherError |
| `browser.py` | 73 | `BrowserFetcher` — Playwright Chromium headless, `wait_until=domcontentloaded + networkidle` |
| `factory.py` | 30 | `FallbackFetcher(primary, secondary)` — при 403/429/None-status прозрачно переключается на secondary |

### `encar_parser/utils/`

| Файл | Строк | Назначение |
|---|---|---|
| `log.py` | 41 | `setup_logging()` — structlog с JSONRenderer и ISO-timestamp |
| `rate_limit.py` | 50 | `TokenBucket` (asyncio.Lock, ёмкость + refill/sec), `RandomDelay` (uniform sleep) |
| `ua.py` | 20 | Список из 10 реалистичных UA (Chrome 124-126, Safari 17.4-17.5, Edge, Firefox 126-127) |

### `output/`

| Файл | Назначение |
|---|---|
| `build_export.py` | Из `_raw_3pages.json` + `_details.json` строит `encar_export.csv` (23 колонки), `encar_export.html` (карточки + lightbox), `encar_export.jsonld.json` (Schema.org Vehicle) |
| `download_photos.py` | Скачивает все фото из `_details.json` в `photos/{carid}/`. async, concurrency=8, пропускает уже скачанные |

### Docker / деплой

| Файл | Назначение |
|---|---|
| `Dockerfile` | Multi-stage: `base` (apt + uv sync + app code) → `cron` (crontab + entrypoint.sh) |
| `docker-compose.yml` | `postgres:16-alpine` + `parser` (build из cron stage). `models.yaml` read-only bind |
| `docker/cron/entrypoint.sh` | `alembic upgrade head` → `python -m encar_parser sync` → `exec cron -f` |
| `docker/cron/crontab` | `0 3 * * * cd /app && uv run --no-sync python -m encar_parser run >> /var/log/encar.log 2>&1` |

### Тесты

| Файл | Строк | Покрытие |
|---|---|---|
| `tests/unit/test_encar_url.py` | 65 | build_q, build_sr, build_list_api_url, safe=`()._` (баг с `\|`) |
| `tests/unit/test_parsers.py` | 167 | legacy shape + real shape + edge cases (Y/N car_type, body, accident) |
| `tests/unit/test_translations.py` | 68 | KO→RU по 4 словарям + проход неизвестных значений |
| `tests/unit/test_api_fetcher.py` | 39 | httpx через respx: 200, 403, 429, timeout, JSON parse |
| `tests/unit/test_browser_fetcher.py` | 15 | моки Playwright |
| `tests/unit/test_db_models.py` | 56 | схема + индексы |
| `tests/unit/test_repository.py` | 78 | upsert идемпотентность, link_car_to_model |
| `tests/unit/test_scheduler.py` | 52 | 3-day rotation: (1,4,7), (2,5), (3,6) — корректные распределения |
| `tests/unit/test_rate_limit.py` | 26 | TokenBucket + RandomDelay |
| `tests/unit/test_config.py` | 21 | Settings из .env |
| `tests/unit/test_ua.py` | 11 | пул UA — все валидные строки |
| `tests/unit/test_fetcher_protocol.py` | 25 | оба fetcher'а реализуют Protocol |
| `tests/unit/test_fetcher_factory.py` | 62 | FallbackFetcher: 200 → primary, 403 → secondary, 500 → raise |
| `tests/integration/test_pipeline.py` | 91 | end-to-end на sqlite in-memory + respx |
| `tests/e2e/test_smoke_live.py` | 26 | **live**: реальный fetch bmw-x5-g05 → assert items > 0 |

---

## 5. Парсинг: метод, эндпоинты, селекторы, лимиты

### Метод

**Гибридный (httpx → Playwright fallback).** Парсер НЕ использует HTML-парсинг (BeautifulSoup/lxml) и НЕ использует CSS/XPath-селекторы — encar отдаёт JSON через свой внутренний API, фронтенд на Vue подгружает данные ровно оттуда же.

### Реальные эндпоинты (обнаружены в live-тестах 2026-06-17)

| Назначение | URL | Метод |
|---|---|---|
| **Список объявлений** | `https://api.encar.com/search/car/list/general?count=true&q=<S-expr>&sr=<sort\|off\|limit>` | GET |
| **Карточка авто** | `https://api.encar.com/v1/readside/vehicle/{encar_id}` | GET |
| **Фото** | `https://ci.encar.com{path}` (например `/carpicture03/pic4213/42131435_001.jpg`) | GET |
| **Фронтенд (референс для Referer)** | `https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!{action}` | GET |
| **Ссылка на карточку (для UI)** | `https://fem.encar.com/cars/detail/{id}` | — (генерируется, не фетчится) |
| **Кнопка "Открыть на EncAr"** | `https://www.encar.com/dc/dc_carsearchview.do?carid={id}` | — (генерируется) |

### Формат `q` (фильтр-выражение)

S-expression из category cells:
```
(And.(C.CarType.Y._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5 (G05).))))
```

Ключевые биты:
- `Hidden.N._.` — внешняя обёртка (обязательна в реальном API)
- `C.CarType.{N|Y}` — `N` для корейского рынка / импортируемых, `Y` для подержанных. **НЕ** путать с `for`/`kor` (это front-end параметр)
- `C.Manufacturer.<name>`, `C.ModelGroup.<name>` — с префиксом `C.`
- `Model.<name>` — **без** префикса `C.` (вложенный уровень)
- `Year.range(YYYYMM..YYYYMM)` — 6-значный year-month (`201800` = январь 2018)

**Сырой escape hatch:** поле `raw_q` в `models.yaml` — если задан, используется **как есть** вместо сгенерированного. Для `bmw-x5-g05` уже сохранён реальный `q` из DevTools.

### Формат `sr` (sort + pagination)

```
|ModifiedDate|0|20
 ^sort code   ^offset ^limit
```

Конкретные коды сортировки: `ModifiedDate`, `PriceAsc`, `PriceDesc`, `MileageAsc`, `Year`.

**⚠️ Критичный баг (уже исправлен 2026-06-17):** `|` в `sr` должен percent-кодироваться как `%7C`. Если `safe="()._|"` в `urllib.parse.urlencode` — encar возвращает HTTP 400. Текущий код: `safe="()._"` — корректно.

### Пагинация

Через `sr`: `offset = (page - 1) * limit`. `ModelConfig.limit` (default 20, max 100). Реальная пагинация пока не реализована в `pipeline.py` — `run_model` обходит только первую страницу. Для 100+ моделей с сотнями объявлений каждая это потолок ~2000 авто за запуск. **TODO: пагинация.**

### Лимиты, задержки, защита от бана

| Параметр | Значение | Где настраивается |
|---|---|---|
| Задержка между запросами внутри модели | `random.uniform(2.0, 5.0)` сек | `Settings.min_delay_sec/max_delay_sec` |
| Задержка между моделями | `random.uniform(5.0, 15.0)` сек | `Settings.min_model_delay_sec/max_model_delay_sec` |
| Ротация User-Agent | 10 строк (Chrome/Safari/Edge/Firefox) | `utils/ua.py` |
| Accept-Language | `ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7` | `ApiFetcher.__aenter__` |
| Referer | `https://www.encar.com/fc/fc_carsearchlist.do` | `Settings.encar_referer` |
| Origin | `https://www.encar.com` | hardcoded в `ApiFetcher` |
| Retry | 3 попытки (tenacity) при 5xx/429/timeout | декларировано в spec, в коде НЕ реализовано (см. bugs) |
| Rate-limit | ~1200 req/час получается естественно при задержках | `Settings.rate_limit_per_hour` (настраивается, не enforcement) |
| Прокси | **НЕТ** | — (отложено по решению архитектуры) |

### Заголовки HTTP (ApiFetcher)

```
User-Agent: <random из пула>
Referer: https://www.encar.com/fc/fc_carsearchlist.do
Origin: https://www.encar.com
Accept: application/json, text/plain, */*
Accept-Language: ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7
```

### Заголовки HTTP (BrowserFetcher)

```
User-Agent: <random из пула> (на уровне контекста Playwright)
Accept-Language: ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7
locale: ko-KR
```

`wait_until="domcontentloaded"` + `wait_until="networkidle"` (timeout 15 сек).

---

## 6. Модель данных и выгрузка

### Схема БД (PostgreSQL 16, 4 таблицы)

**`search_models`** — справочник поисковых фильтров
```
id              SERIAL PK
slug            TEXT UNIQUE NOT NULL
name            TEXT NOT NULL
encar_url       TEXT NOT NULL              -- полный URL фильтра
encar_action    JSONB NOT NULL             -- разобранный action
enabled         BOOLEAN DEFAULT TRUE
priority        INT DEFAULT 100
last_run_at     TIMESTAMPTZ
created_at      TIMESTAMPTZ DEFAULT NOW()
```

**`cars`** — собранные авто
```
encar_id              BIGINT PK             -- 42131435
brand                 TEXT NOT NULL
model                 TEXT NOT NULL
year_month            DATE                  -- 2025-11-01
mileage_km            INT
displacement_cc       INT
fuel_ru               TEXT                  -- "Бензин"
fuel_original         TEXT                  -- "가솔린"
transmission_ru       TEXT                  -- "Автомат"
transmission_orig     TEXT                  -- "오토"
body_type             TEXT                  -- "SUV"
color_ru              TEXT
color_original        TEXT
seats                 INT
import_type_ru        TEXT                  -- "Официальный"
manufacturer_warranty TEXT
liens_seizures        TEXT                  -- "0건·0건"
accident_records      INT                   -- 0 или 1
plate_number          TEXT
price_krw             BIGINT
photo_urls            JSONB                 -- ["https://...", ...]
encar_detail_url      TEXT
first_seen_at         TIMESTAMPTZ DEFAULT NOW()
last_seen_at          TIMESTAMPTZ
raw_data              JSONB                 -- полный JSON для отладки
```

Индексы: `idx_cars_brand_model`, `idx_cars_year_month`, `idx_cars_price_krw`.

**`car_model_matches`** — связь many-to-many (одно авто в нескольких моделях)
```
search_model_id  INT FK → search_models(id) ON DELETE CASCADE
encar_id         BIGINT FK → cars(encar_id) ON DELETE CASCADE
first_matched_at TIMESTAMPTZ DEFAULT NOW()
last_matched_at  TIMESTAMPTZ
PRIMARY KEY (search_model_id, encar_id)
```

**`runs`** — журнал запусков
```
id              SERIAL PK
started_at      TIMESTAMPTZ DEFAULT NOW()
finished_at     TIMESTAMPTZ
models_planned  INT
models_done     INT
cars_fetched    INT
cars_failed     INT
error_log       JSONB
```

### Словари переводов KO → RU

| Поле | KO-вход | RU-выход |
|---|---|---|
| Топливо | 가솔린 / 디젤 / 하이브리드 / 전기 / LPG / 가스 | Бензин / Дизель / Гибрид / Электро / Газ / Газ |
| КПП | 오토 / 자동 / 수동 / CVT / DCT / 로봇 | Автомат / Автомат / Механика / Вариатор / Робот / Робот |
| Цвет | 검정색 / 흰색 / 회색 / 은색 / 파란색 / 빨간색 / 노란색 / 녹색 / 갈색 / 보라색 | Чёрный / Белый / Серый / Серебристый / Синий / Красный / Жёлтый / Зелёный / Коричневый / Фиолетовый |
| Импорт | 정식수입 / 병행수입 / REGULAR_IMPORT / PARALLEL_IMPORT | Официальный / Параллельный (×2) |

Неизвестные значения проходят насквозь (без падения).

### Выходные форматы (output/)

**1. `encar_export.csv`** — 30 строк × 23 колонки
```
id, brand, model, badge, year_month, form_year, mileage_km,
price_10k_krw, price_krw, fuel, transmission, color, body, seats,
city, photo_count, main_photo, photo_urls, detail_url, view_on_encar,
accident_record, pledge_count, seizing_count
```
Кодировка UTF-8-sig (BOM для Excel). Фото через `;`.

**2. `encar_export.html`** — статический каталог 30 машин
- Карточка: main фото + 3 thumbnails, specs (год, пробег, топливо, КПП, цвет, места, кузов), цена в 만원 + KRW, кнопки "Открыть на EncAr" / "fem.encar"
- Lightbox с prev/next навигацией (CSS-only, на якорях)
- Встроенный JSON-LD (Schema.org Vehicle) для SEO

**3. `encar_export.jsonld.json`** — Schema.org Vehicle[] для прямого импорта

**4. `output/photos/{carid}/`** — зеркало фото (если `download_photos.py` запускался; источник — `ci.encar.com`)

### Источник истины для моделей

`models.yaml` — YAML-файл с 3 активными моделями на момент сканирования:
- `bmw-x5-g05` (BMW X5 G05, приоритет 10, есть `raw_q` из реального браузера)
- `kia-sportage-nq5` (Kia Sportage NQ5, приоритет 20)
- `hyundai-sonata-dn8` (Hyundai Sonata DN8, приоритет 30)

`encar-parser sync` читает YAML → upsert в `search_models`. Модели, отсутствующие в YAML, помечаются `enabled=false` (история сохраняется).

---

## 7. Конфиг и секреты

**Реальных секретов в репозитории нет** — все credentials либо пустые, либо дефолтные dev-значения. Проверены все потенциальные места:

| Файл / место | Что есть | Статус |
|---|---|---|
| `.env.example` | `DATABASE_URL=postgresql+asyncpg://encar:encar@localhost:5432/encar` | **dev default**, не продакшн |
| `.env.example` | `DB_PASSWORD` (через docker-compose) | **отсутствует**, дефолт `encar` |
| `.env.example` | `SENTRY_DSN=` (пустая) | `***REDACTED***` (пусто) |
| `models.yaml` | Нет credentials | — |
| `encar_parser/config.py` | `api_list_base`, `api_detail_template`, `encar_referer` | URL endpoints, не секреты |
| `encar_parser/utils/ua.py` | 10 публичных User-Agent строк | публичные |
| `docker-compose.yml` | `${DB_PASSWORD:?}` — обязательная переменная | требует установки в `.env` |
| `Dockerfile` | `TZ=Asia/Seoul` | не секрет |

**Что присутствует как потенциально-секретное место:**
- `DATABASE_URL` в `.env` — содержит пароль БД → `***REDACTED***`
- `SENTRY_DSN` в `.env` — пустая, но при заполнении → `***REDACTED***`
- Любой `DB_PASSWORD` в `/opt/encar/.env` (на VPS после деплоя) — `***REDACTED***`, `chmod 600`

**Где документально упоминаются секреты для будущего:**
- `deploy/encar.env.example` (в плане, ещё не создан) — будет шаблон для production
- `DEPLOY.md` (в плане) — инструкция «deploy.sh генерирует случайный пароль через `openssl rand -hex 24`»

**Все `***REDACTED***` выше — пустые строки в реальном репозитории.** Заполняются при деплое.

---

## 8. Внешние зависимости и сторонние API

### Сторонние API (используются)

| Сервис | Назначение | Auth | Требования к сети |
|---|---|---|---|
| `api.encar.com` | Основной парсинг (список + детали) | Нет | Прямой HTTPS GET, корректный Referer/Origin |
| `ci.encar.com` | Скачивание фотографий | Нет | Прямой HTTPS GET, рабочий CDN (img.encar.com фильтруется) |
| `www.encar.com` | Только для Referer-заголовка | Нет | — |

### Внешние сервисы (НЕ используются, но зарезервированы)

| Сервис | Где упоминается | Статус |
|---|---|---|
| Sentry | `Settings.sentry_dsn` (пустая) | Опционально, не подключён в коде |
| Telegram / email | — | НЕ реализовано (за пределами MVP по решению) |
| Прокси-пулы | `Fetcher` Protocol это допускает | НЕ реализовано (отложено) |

### NPM-пакеты / JS-зависимости

**Нет.** Проект чисто Python + Bash.

### Системные зависимости (runtime в Docker)

- `build-essential`, `libpq-dev` (для сборки колёс)
- `curl` (для healthcheck)
- `cron` (для расписания)
- `tzdata` (в новой версии Dockerfile, ещё не вшитом)

---

## 9. Установка и запуск

### Локально (для разработки)

```bash
# 1. Клонировать и поставить зависимости
git clone <repo> encar-parser && cd encar-parser
make install
#   - uv sync --extra dev
#   - uv run playwright install chromium

# 2. Настроить окружение
cp .env.example .env
# По умолчанию DATABASE_URL указывает на localhost:5432

# 3. Запустить PostgreSQL (через Docker или локально)
docker compose up -d postgres

# 4. Применить миграции
make migrate
#   - uv run alembic upgrade head

# 5. Синхронизировать models.yaml → БД
make sync
#   - uv run python -m encar_parser sync

# 6. Запустить парсер
make run
#   - uv run python -m encar_parser run
#   - берёт модели на сегодня по 3-day rotation

# 7. Тесты
make test              # unit + integration (по умолчанию)
make test-live         # + e2e на реальном encar
```

### В Docker (для production / VPS)

```bash
# Поднять стек
docker compose up -d
#   - postgres (volume pgdata)
#   - parser (cron: 0 3 * * *, mem_limit=1 GiB)

# Логи
docker compose logs -f parser

# Ручной запуск (вне расписания)
docker compose exec parser uv run --no-sync python -m encar_parser run
# Ограничить прогон для теста памяти:
#   --max-models 10   # только первые 10 моделей из сегодняшнего среза
#   --max-pages  3    # максимум 3 страницы по 20 машин на модель

# Миграции
docker compose exec parser uv run --no-sync alembic upgrade head
```

### Память парсера (Phase 3, OOM fix)

Парсер раньше падал с кодом 137 (kernel OOM-kill) на полном списке
моделей. Причины и фиксы:

**Корневые причины:**

1. **Playwright Chromium всегда жив в парсере** — `BrowserFetcher`
   создаётся один раз в начале `run()` как fallback на 403/429. Даже
   когда API работает, Chromium-процессы (main + zygote + GPU + utility)
   висят в памяти ≈120 MiB. Это baseline overhead, не лик.
2. **httpx default connection pool** = 100 keepalive — без нужды для
   однопоточного парсера. Поставлено `max_connections=20,
   max_keepalive_connections=10`.
3. **`error_log` в `runs` мог расти неограниченно** — теперь cap = 50
   записей, остальные считаются в `suppressed_errors`.
4. **Нет `mem_limit` в docker-compose** — контейнер мог съесть всю
   RAM хоста до того, как OOM-killer сработает на уровне ядра. Теперь
   `mem_limit: 1g`, `mem_reservation: 512m`, `restart: on-failure:5`.

**Что НЕ оказалось проблемой** (проверено диагностическим
скриптом `scripts/diagnose_memory.py`):

- SQLAlchemy identity map — weak refs, GC собирает после commit
- asyncio.gather в pipeline — нет, обработка последовательная
- Накопление ORM объектов — `session.expunge_all()` после каждой
  модели дополнительно подчищает

**Что добавлено:**

- `encar_parser/memlog.py` — фоновый sampler, логирует RSS каждые
  60 сек и в конце выдаёт peak. Каждый прогон пишет `memlog_summary`
  в `encar.log`.
- `MemSampler.start()/stop()` оборачивает весь `run()`.
- `--max-models` и `--max-pages` флаги для тестовых прогонов.

**Проверено на dev (15 моделей × 3 страницы = ~900 машин):**

- Container RSS стабильно ~210 MiB из 1 GiB (21%)
- Process RSS стабильно ~92 MiB
- Нулевой рост по 274+ обработанным машинам
- Прогон укладывается в `mem_limit` с большим запасом

**На проде:**

- Полный прогон ~100 моделей × 1000 машин = 100k машин. По текущим
  данным ~1.9 KiB/машина → ожидаемый рост 100k × 1.9 KiB = ~190 MiB.
  С baseline 210 MiB пик ~400 MiB. `mem_limit: 1g` комфортно.
- При первом продовом запуске следить за `docker stats parsingencar-parser-1`.
  Если RSS приближается к 800 MiB — `docker compose logs parser` покажет
  `memlog_tick` и поможет найти место роста.

### Экспорт (output/)

```bash
# Из уже скачанных данных (без сети)
.venv/bin/python output/build_export.py
# → encar_export.csv, .html, .jsonld.json

# Скачать фото (зеркало ci.encar.com)
.venv/bin/python output/download_photos.py
# → output/photos/{carid}/*.jpg

# Перегенерировать с локальным зеркалом
.venv/bin/python output/build_export.py
```

### Команды CLI (Typer)

```bash
uv run encar-parser sync                    # YAML → БД
uv run encar-parser run                     # парсинг на сегодня (+ dedup в конце)
uv run encar-parser migrate                 # alembic upgrade head
uv run encar-parser dedup                   # переcхлопнуть дубли (идемпотентно)
uv run encar-parser dedup --json            # JSON-отчёт: groups / hidden / primary
uv run encar-parser probe bmw-x5-g05        # живой smoke-тест списка
uv run encar-parser probe bmw-x5-g05 --detail-id 42131435   # + деталь
```

### Дедупликация (Phase 2)

Encar часто вешает одну и ту же физическую машину под несколькими
`encar_id` (разные объявления, одинаковые фото и спеки). Витрина и CRM
должны показывать каждую машину **один раз**; строки в БД не удаляются —
они просто помечаются `is_primary = false`.

**Ключ дедупа** — кортеж `(brand, model, year_month, mileage_km, color_original)`
все пять полей non-NULL. Дополнительно: если у двух машин полностью совпадают
`photo_urls` (фоллбэк для машин с NULL-полями). Внутри группы основным
считается строка с **максимальным `encar_id`** (самое свежее объявление).

**Прогон:**

```bash
# Ручной запуск (можно гонять когда угодно, идемпотентно)
docker compose exec -T web uv run --no-sync python -m encar_parser dedup

# В пайплайне уже автоматически — `python -m encar_parser run` после
# всех моделей зовёт dedup, так что свежие объявления сразу схлопываются.
```

**Проверка в БД:**

```sql
-- Сколько групп, сколько спрятано, сколько уникальных машин
SELECT is_primary, count(*) FROM cars GROUP BY is_primary;
--   t |   62     <- уникальные машины в витрине
--   f |   38     <- спрятанные дубли
```

**Деплой:** на каждом sync'е с Mac на сервер нужно заново
`docker compose up -d --build web` (новый код в образе), и затем
`alembic upgrade head` — миграция `0003_is_primary` добавляет колонку
+ partial index `idx_cars_is_primary`.

### Расписание парсинга (Phase 4)

Каталог ~105 моделей × ~469 машин в среднем = **~49 200 машин всего**;
детали тянутся ~4 сек/машина. Полный обход ≈ 55 часов — за сутки никак.
Поэтому планировщик делит каталог на ротацию и ходит по ней инкрементально.

**Ротация:** все enabled-модели сортируются по `(priority, slug)` и
делятся на N бакетов. Бакет на сегодня = `(day_of_year − 1) % N`.
После полного backfill по умолчанию стоит N=14 (≈ 7-8 моделей в день,
худший день ~7 ч, помещается в 12-часовой бюджет с большим запасом).

**Cooldown:** даже если модель в сегодняшнем бакете, она пропускается,
если `last_run_at` моложе `scheduler_cooldown_hours` (по умолчанию 12).
Это защищает от повторного парсинга модели, которая только что отработала
в соседнем бакете.

**Anti-ban:**
- `RandomDelay(2.0, 5.0)` сек между детальными запросами (settings)
- `RandomDelay(5.0, 15.0)` сек между моделями
- tenacity retry на 429/5xx (3 попытки с экспоненциальным backoff)
- Если 4-секундный `per_car_sec` слишком быстрый — увеличить через
  `settings.min_delay_sec`/`max_delay_sec` или env

**Инкрементальный режим** (`run-incremental`): API отдаёт машины в
порядке `ModifiedDate` (новые первые). Пайплайн идёт по страницам и
останавливается, как только самый новый автомобиль на странице был
уже виден в течение cooldown-окна. На хорошо обкатанной БД
инкрементальный забег обрабатывает 0-200 машин и финиширует за <1 час.

**Backfill** (`backfill`): полный обход всей ротации. Состояние
сохраняется в `/var/log/backfill_state.json` (atomic write, version 1)
после каждой модели — на рестарте/краше продолжает с места, а не с нуля.
`--reset` стирает state и начинает сначала. Запускается только руками
(НЕ в cron).

**План / dry-run** (`plan`): без сети показывает, что будет парситься
сегодня (или в произвольный день/диапазон), сколько машин, сколько
времени при 4 сек/машина. С ключом `--probe` живо опрашивает Encar
на Count (≈3 сек × 105 моделей ≈ 5 мин) и сохраняет кэш в
`/var/log/encar_counts.json`. Без кэша считает по среднему (469).

**Команды CLI:**

```bash
# План (быстрый, без сети — использует кэш /var/log/encar_counts.json)
uv run encar-parser plan                          # сегодня, человекочитаемо
uv run encar-parser plan --days 14                # полная 14-дневная ротация
uv run encar-parser plan --json                   # JSON
uv run encar-parser plan --probe                  # обновить кэш counts
uv run encar-parser plan --day 2026-06-21         # конкретная дата

# Backfill (ручной, resumable)
uv run encar-parser backfill                      # с resume
uv run encar-parser backfill --reset -y           # с нуля (destructive!)
uv run encar-parser backfill --max-pages 5        # обрезать каждый model walk

# Ежедневный инкремент (вызывается cron'ом в 03:00 KST)
uv run encar-parser run-incremental
uv run encar-parser run-incremental --cooldown-hours 6
uv run encar-parser run-incremental --max-models 5 --max-pages 3   # тест
```

**Cron** (`docker/cron/crontab`):

```
0 3 * * * cd /app && uv run --no-sync python -m encar_parser run-incremental >> /var/log/encar-incremental.log 2>&1
```

Backfill в cron НЕ включён — это ручная операция.

**Измеренные цифры (Phase 4 probe, 2026-06-21):**

| Сценарий | Цифра |
|----------|-------|
| Всего моделей (enabled) | 105 |
| Всего машин в каталоге | 49 217 |
| Median машин/модель | 114 |
| Top-1 (g80-rg3 / electrified-g80-rg3) | 3 362 каждый |
| Top-15 суммарно | 28 665 (58% от всех) |
| Полный обход @ 4 сек/машина | **~55 часов** |
| 7-дневная ротация, худший день | 9.4 ч (тесно в сутках) |
| **14-дневная ротация, худший день** | **6.87 ч** ✅ (с запасом) |
| Инкремент на хорошо обкатанной БД | 0-200 машин/день, <1 ч |

**Включение на проде (порядок строго соблюдать):**

1. `rsync` + `alembic upgrade head` + `docker compose up -d --build web parser`
   (Phase 2 + Phase 4 — `web` подхватит код планировщика, `parser` —
   новый crontab).
2. **`python -m encar_parser backfill --max-models 5 --max-pages 2`** —
   ручной тест: 5 моделей, по 40 машин = 200 машин, ~15 мин. Смотреть
   `docker stats parsingencar-parser-1` — RSS должен держаться 200-250 MiB
   (как на Phase 3 verification), не расти линейно.
3. Если ОК — **`python -m encar_parser backfill --yes`** — полный backfill,
   resumable. На сервере 1042 строки → бэкфилл пройдёт по тем же моделям,
   которые уже есть, частично skip'нет, частично дотянет свежие.
4. После завершения backfill — **включить cron** (он уже прописан в
   crontab, но `restart: on-failure:5` не даст контейнеру стартовать,
   если entrypoint крашнется):
   ```bash
   # Проверить что cron внутри контейнера видит crontab:
   ssh user@vps 'docker compose exec parser crontab -l'
   # Триггернуть первый инкремент вручную:
   ssh user@vps 'docker compose exec parser uv run --no-sync python -m encar_parser run-incremental'
   # Смотреть /var/log/encar-incremental.log
   ```
5. **Проверка через неделю:** `python -m encar_parser plan` — что в
   сегодняшнем бакете? Должны быть модели, которые еще не парсились
   сегодня; ничего не должно валиться с OOM.

---

## 10. Известные баги, TODO, что не работает

### Известные баги / ограничения

1. **`img.encar.com` заблокирован с этой машины разработки** (SSL handshake таймаутит — CDN фильтруется), но `ci.encar.com` работает с этой же машины (200 OK, проверено 2026-06-20). Поэтому в коде хоста фото переключили на `ci.encar.com` — это правильный CDN для бинарных фото; `img.encar.com` больше не используется пайплайном.

2. **2 ошибки mypy** (известны, не блокеры, отмечены в прогрессе):
   - `encar_parser/fetchers/browser.py:24` — Playwright type annotation
   - `encar_parser/cli.py:13` — missing yaml stubs (нужно `types-PyYAML` или `ignore_missing_imports`)

3. **Ruff warnings** (в основном auto-fixable): unsorted imports, unused imports, `Session` uppercase variable, длинные строки в тестах. Не блокер, не критично.

4. **E2E-тест НЕ auto-skip'ится в CI** — `pytest` не пропускает кастомные маркеры автоматически. Нужно явно `pytest -m "not live"` в CI-конфиге (его пока нет).

5. **Tenacity retry НЕ подключён в коде**, хотя `tenacity` есть в `pyproject.toml` и спека обещает «3 попытки, экспонента 5–60 сек на 5xx/429/timeout». В `ApiFetcher.get` при 429 сразу выбрасывается `FetcherError` — никакого ретрая. Fallback на Playwright срабатывает через `FallbackFetcher`, что частично компенсирует.

6. **Пагинация не реализована.** `pipeline.run_model` берёт только первую страницу (`page=1` по умолчанию). Для моделей с сотнями объявлений получается обрезка.

7. **`rate_limit_per_hour` не enforcement.** Поле есть в `Settings`, но `TokenBucket` нигде не вызывается в `pipeline.py` — есть только `RandomDelay`. Число запросов в час контролируется только задержками.

8. **10 модифицированных файлов не закоммичены** с 2026-06-17 (после live-фикса API): `encar_url.py`, `parsers/details.py`, `parsers/list_page.py`, `translations.py`, `fetchers/api.py`, `cli.py`, `config.py`, `models.yaml`, `tests/unit/test_encar_url.py`, `tests/unit/test_parsers.py`, плюс `CHANGES_RU.md` и `output/*.py` — untracked.

9. **E2E тест устарел по shape'у.** `tests/e2e/test_smoke_live.py` использует legacy-парсер URL-а (`build_url(cfg)` возвращает актуальный, но `test_parsers.py` уже с real shape — должны быть синхронизированы).

### TODO / отложено

- **Прокси-пул** — архитектура готова (`Fetcher` Protocol), но `ProxyFetcher` не написан.
- **100+ моделей** в `models.yaml` — сейчас 3, остальные добавляются постепенно.
- **Админка / дашборд** — за пределами MVP.
- **Telegram/email-уведомления** — не реализовано.
- **Опциональные коды** `options.standard` (001, 002, ...) — не декодированы в человекочитаемые имена.
- **Warranty limits** — `category.warranty.month/mileage` не вытаскиваются, только `companyName`.
- **Body type taxonomy** — не подтверждено покрытие седанов/хэтчбеков (пока только SUV в фикстурах).
- **Регион (지역)** — отсутствует в реальном API (подтверждено, не блокер).
- **Мощность (л.с.)** — отсутствует на encar (подтверждено, не блокер).
- **«짐» (багаж)** — отсутствует в реальном API (подтверждено, не блокер).
- **`models.yaml` валидация** — нет схемы (JSON Schema / Pydantic), typos проходят silently. `ModelConfig` парсит, но `year_from > year_to` и т.п. не валидируются.

### Не реализовано, но задокументировано

- **CI** — нет GitHub Actions / GitLab CI. `Makefile` есть, но `make test` надо запускать руками.
- **Мониторинг** — только JSON-логи в stdout. Sentry-заглушка пустая.
- **Backup БД** — не автоматизирован (только ручной `pg_dump`).
- **`scripts/record-fixtures.py`** — упомянут в `Makefile` (`record-fixtures`), но в `cli.py` его нет. Не работает.

---

## 11. Полное содержимое ключевых файлов парсера

### Файл 1: `encar_parser/encar_url.py` (150 строк) — ядро: YAML → URL encar API

```python
"""Build encar.com search requests from a ModelConfig.

Encar exposes an internal JSON API that its own front-end calls:

    https://api.encar.com/search/car/list/general?count=true&q=<filter>&sr=<sort>

`q` is an S-expression filter built from "category cells":

    (And.(C.CarType.Y._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5 (G05).))))

`sr` encodes sort + pagination:  |ModifiedDate|<offset>|<limit>

This module builds that API URL. It also keeps a human-readable front-end URL
(www.encar.com/...#!...) purely for reference / Referer headers.

NOTE: encar can change CarType codes and field names. If a query returns 0
results, capture the real `q` from your browser devtools (Network tab) and paste
it into models.yaml as `raw_q:` — it overrides the generated filter.
"""

from __future__ import annotations

import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field

EncCarType = Literal["for", "kor"]  # for = imported, kor = domestic (front-end carType)
SortOrder = Literal["ModifiedDate", "PriceAsc", "PriceDesc", "MileageAsc", "Year"]

API_LIST_BASE = "https://api.encar.com/search/car/list/general"
FRONTEND_BASE = "https://www.encar.com/fc/fc_carsearchlist.do"

# Map a few friendly sort names to encar's sort codes used in `sr=|<code>|...`.
_SORT_CODES = {
    "ModifiedDate": "ModifiedDate",
    "PriceAsc": "PriceAsc",
    "PriceDesc": "PriceDesc",
    "MileageAsc": "MileageAsc",
    "Year": "Year",
}


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

    fuel: str | None = None
    transmission: str | None = None
    body_type: str | None = None

    price_from: int | None = None
    price_to: int | None = None
    mileage_to: int | None = None

    car_type: EncCarType = "for"
    sort: SortOrder = "ModifiedDate"
    limit: int = Field(default=20, ge=1, le=100)

    # Escape hatch: a raw `q` filter copied verbatim from devtools. When set,
    # it is used as-is and the generated filter is ignored.
    raw_q: str | None = None
    # Encar CarType cell code used inside `q` (Y/N/etc). Defaults are a best
    # guess; override here if results look wrong.
    car_type_code: str = "Y"


def _cell(field: str, value: str) -> str:
    """A single category cell: C.<Field>.<Value>"""
    return f"C.{field}.{value}"


def build_q(cfg: ModelConfig) -> str:
    """Build the nested S-expression `q` filter for the encar API.

    Produces e.g.:
        (And.(C.CarType.Y._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5 (G05).))))
    """
    if cfg.raw_q:
        return cfg.raw_q

    cells: list[str] = [_cell("CarType", cfg.car_type_code)]
    if cfg.manufacturer:
        cells.append(_cell("Manufacturer", cfg.manufacturer))
    if cfg.model_group:
        cells.append(_cell("ModelGroup", cfg.model_group))
    if cfg.model:
        # The deepest level uses the bare field name (no leading C.) in encar's
        # format, e.g. ..._.Model.X5 (G05).
        cells.append(f"Model.{cfg.model}")

    # Nest the cells: (A._.(B._.(C._.D)))
    expr = cells[-1]
    for cell in reversed(cells[:-1]):
        expr = f"{cell}._.({expr})"
    return f"(And.({expr}.))"


def build_sr(cfg: ModelConfig, *, page: int = 1) -> str:
    """Build the `sr` sort+pagination string: |<sort>|<offset>|<limit>."""
    sort_code = _SORT_CODES.get(cfg.sort, "ModifiedDate")
    offset = (max(page, 1) - 1) * cfg.limit
    return f"|{sort_code}|{offset}|{cfg.limit}"


def build_list_api_url(cfg: ModelConfig, *, page: int = 1, count: bool = True) -> str:
    """Build the full encar list API URL that returns JSON."""
    params = {
        "count": "true" if count else "false",
        "q": build_q(cfg),
        "sr": build_sr(cfg, page=page),
    }
    # `safe` excludes `|` — encar wants the sort/pagination token in `sr`
    # percent-encoded (e.g. %7C), not as a literal pipe.
    query = urllib.parse.urlencode(params, safe="()._", quote_via=urllib.parse.quote)
    return f"{API_LIST_BASE}?{query}"


def build_frontend_url(cfg: ModelConfig) -> str:
    """Human-facing search URL (for reference / Referer header)."""
    return f"{FRONTEND_BASE}?carType={cfg.car_type}"


# Backwards-compatible names used elsewhere in the codebase. `build_url` now
# returns the API URL we actually fetch.
def build_url(cfg: ModelConfig) -> str:
    return build_list_api_url(cfg)


def build_action(cfg: ModelConfig) -> dict:
    """Reference payload stored alongside the model (for debugging/inspection)."""
    return {
        "q": build_q(cfg),
        "sr": build_sr(cfg),
        "api_url": build_list_api_url(cfg),
        "frontend_url": build_frontend_url(cfg),
        "sort": cfg.sort,
        "limit": cfg.limit,
    }
```

---

### Файл 2: `encar_parser/fetchers/api.py` (85 строк) — HTTP-клиент с ротацией UA

```python
"""HTTP fetcher using httpx. Primary fetcher for the incar parser."""

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
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
                "Origin": "https://www.encar.com",
                "Referer": self._settings.encar_referer,
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

---

### Файл 3: `encar_parser/pipeline.py` (128 строк) — главный цикл обработки одной модели

```python
"""Main pipeline: run a single model end-to-end."""

from __future__ import annotations

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

---

## 12. Деплой на сервер

Серверный путь — **`/opt/encar`**, **не** git-репозиторий. Синхронизируем rsync'ом с мака, **обязательно** запускаем миграции после каждой выкатки (иначе web-контейнер падает с `UndefinedColumnError`, реальный кейс: `accident_report_available` против старого `accident_records`).

### 1. Синхронизация файлов (с мака)

```bash
rsync -av --exclude='.env' --exclude='.venv' --exclude='.git' \
  --exclude='__pycache__' --exclude='.mypy_cache' --exclude='.ruff_cache' \
  --exclude='.pytest_cache' --exclude='*.pyc' \
  "/Users/mac/ClaudeProjects/parsing encar/"  user@vps:/opt/encar/
```

- `--exclude='.env'` — **load-bearing**: не даёт затоптать серверный `.env` с реальным `DB_PASSWORD`.
- `user@vps` — твой SSH-алиас; не хардкодим.
- После rsync на сервере сразу делаем шаг 2 (миграции), иначе web не стартанёт.

### 2. Миграции (на сервере, **обязательно**)

```bash
ssh user@vps 'cd /opt/encar && docker compose exec -T web uv run --no-sync alembic upgrade head'
```

Это **не опционально**. Если код ссылается на колонку, которой ещё нет в БД (например, переименовали `accident_records` → `accident_report_available`), web-контейнер упадёт на первом же запросе с `UndefinedColumnError`.

### 3. Перезапуск web

```bash
ssh user@vps 'cd /opt/encar && docker compose up -d --build web'
```

Парсер не поднимаем — он отдельно, и если он OOM-ит (был такой кейс), не блокирует витрину.

### 4. Smoke-test после выкатки

```bash
ssh user@vps 'cd /opt/encar && docker compose ps'                            # web + postgres Up
ssh user@vps 'cd /opt/encar && docker compose logs --tail=20 web'              # без DB-ошибок
ssh -L 8090:127.0.0.1:8090 user@vps                                          # туннель
curl -s http://127.0.0.1:8090/ | grep -oE 'Машин в БД: <strong>[0-9]+'        # счётчик
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:8090/img?src=https://img.encar.com/carpicture01/pic4181/41815032_003.jpg"
                                                                              # 200 (прокси сам подменит img→ci)
```

### Биндинг портов (безопасность)

- **web**: проброс только `127.0.0.1:8090:8090` в `docker-compose.yml`. Снаружи порт **закрыт**, смотрим через SSH-туннель (`ssh -L 8090:127.0.0.1:8090 user@vps`).
- **postgres**: маппинг портов в compose **полностью убран** (`expose: ["5432"]` для внутренней сети). Контейнеры ходят к нему как `postgres:5432`. Снаружи 5432 закрыт.

### Что НЕ коммитим в репо

- `.env` (на сервере — священный, с реальным `DB_PASSWORD`)
- `output/encar_export.html`, `encar_export.csv`, `encar_export.jsonld.json` (генерируемые)
- `output/_details.json`, `output/_raw_3pages.json` (кэш прогонов)
- `output/photos/` (локальное зеркало фоток, если есть)

---

## Конец отчёта

Файл `PROJECT_REPORT.md` создан. Содержит 11 секций + 3 полных блоков кода (`encar_url.py`, `fetchers/api.py`, `pipeline.py`). Все секреты замаскированы (их в репо и нет — есть только пустые плейсхолдеры). Картинки/фото не читались — сканировались только `.py`, `.yaml`, `.toml`, `.ini`, `.sh`, `Dockerfile`, `Makefile`, `*.md`, `*.json`. Без запуска кода, без модификации файлов.
