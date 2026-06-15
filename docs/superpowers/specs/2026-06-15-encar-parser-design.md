# Enc Car Parser — Design Spec

**Дата:** 2026-06-15
**Статус:** Утверждён пользователем, ждёт плана реализации
**Автор:** Claude (brainstorming session)

---

## 1. Цель

Парсер объявлений с корейского сайта **encar.com** для последующей переноски на собственный сайт-каталог авто. Собираются базовые характеристики + фото + цена. Никаких попыток обойти CAPTCHA или нарушать ToS — нагрузка сознательно минимальная.

**Источник истины:** `https://www.encar.com/fc/fc_carsearchlist.do` (HTML+JS) и `https://fem.encar.com/cars/detail/{id}` (HTML+JS). Прямые API-эндпоинки encar — основной способ, Playwright — fallback.

---

## 2. Архитектура

```
encar-parser/
├── encar_parser/
│   ├── __init__.py
│   ├── cli.py                 # CLI: run / sync / migrate
│   ├── config.py              # pydantic-settings, .env
│   ├── translations.py        # KO → RU словари
│   ├── encar_url.py           # YAML config → encar action JSON
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py          # SQLAlchemy: Car, SearchModel, Run
│   │   ├── repository.py      # CRUD-обёртки
│   │   └── migrations/        # alembic
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py            # Protocol/ABC
│   │   ├── api.py             # httpx → encar JSON
│   │   └── browser.py         # Playwright fallback
│   ├── parsers/
│   │   ├── list_page.py       # JSON списка → list[EncarId]
│   │   └── details.py         # JSON карточки → Car
│   ├── pipeline.py            # основной цикл
│   ├── scheduler.py           # 3-дневная ротация моделей
│   └── utils/
│       ├── log.py             # structlog
│       ├── rate_limit.py      # token-bucket
│       └── ua.py              # User-Agent pool
├── models.yaml                # 100+ фильтров
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── Makefile
├── tests/
│   ├── conftest.py
│   ├── fixtures/              # записанные ответы encar
│   ├── unit/
│   ├── integration/
│   └── e2e/                   # @pytest.mark.live
├── docs/
│   └── superpowers/specs/     # эта папка
└── mockup.html                # визуальный мокап карточки
```

### Поток одного запуска `python -m encar_parser run`

```
1. Загрузить .env, инициализировать БД, логгер
2. Получить список моделей на сегодня (scheduler.models_for_today)
3. Создать запись Run(models_planned=N)
4. Для каждой модели:
   a. Fetcher.get_list(action) → list[EncarId]
   b. Если httpx ловит 403/timeout → переключиться на Playwright
   c. Для каждого ID: Fetcher.get_detail(id) → JSON
   d. parser.details → Car
   e. repository.upsert_car + link_to_model
   f. Задержка 2-5 сек
5. Записать результат Run, отправить сводку в stdout
```

### Ключевые решения по архитектуре

- **`Fetcher` как Protocol** — единый интерфейс `async def fetch(url) -> Response`, чтобы прокси подключались заменой одной строки в `FetcherFactory`.
- **Pipeline** — единственное место, где компоненты склеиваются. Парсеры и fetchers ничего не знают друг о друге.
- **Слои изолированы**: парсеры принимают `dict`/JSON, возвращают dataclass; репозиторий принимает dataclass, возвращает id.

---

## 3. Схема БД (PostgreSQL)

```sql
-- Сохранённый фильтр encar
CREATE TABLE search_models (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,            -- "bmw-x5-g05"
    name          TEXT NOT NULL,                   -- "BMW X5 (G05)"
    encar_url     TEXT NOT NULL,                   -- полный URL фильтра
    encar_action  JSONB NOT NULL,                  -- разобранный action-объект
    enabled       BOOLEAN DEFAULT TRUE,
    priority      INT DEFAULT 100,
    last_run_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Само авто (одно и то же авто может попадать в несколько моделей)
CREATE TABLE cars (
    encar_id              BIGINT PRIMARY KEY,        -- 42131435
    brand                 TEXT NOT NULL,             -- "BMW"
    model                 TEXT NOT NULL,             -- "X5 (G05)"
    year_month            DATE,                      -- 2025-11-01
    mileage_km            INT,
    displacement_cc       INT,
    fuel_ru               TEXT,                      -- "Бензин"
    fuel_original         TEXT,                      -- "가솔린"
    transmission_ru       TEXT,                      -- "Автомат"
    transmission_orig     TEXT,                      -- "오토"
    body_type             TEXT,                      -- "SUV"
    color_ru              TEXT,
    color_original        TEXT,
    seats                 INT,
    import_type_ru        TEXT,                      -- "Официальный"
    manufacturer_warranty TEXT,
    liens_seizures        TEXT,                      -- "0/0"
    accident_records      INT,
    plate_number          TEXT,
    price_krw             BIGINT,
    photo_urls            JSONB,                     -- ["https://...", ...]
    encar_detail_url      TEXT,
    first_seen_at         TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at          TIMESTAMPTZ,
    raw_data              JSONB                      -- полный JSON для отладки
);

-- Связь many-to-many
CREATE TABLE car_model_matches (
    search_model_id  INT REFERENCES search_models(id) ON DELETE CASCADE,
    encar_id         BIGINT REFERENCES cars(encar_id) ON DELETE CASCADE,
    first_matched_at TIMESTAMPTZ DEFAULT NOW(),
    last_matched_at  TIMESTAMPTZ,
    PRIMARY KEY (search_model_id, encar_id)
);

CREATE INDEX idx_cars_brand_model ON cars(brand, model);
CREATE INDEX idx_cars_year_month ON cars(year_month);
CREATE INDEX idx_cars_price_krw ON cars(price_krw);
CREATE INDEX idx_matches_model ON car_model_matches(search_model_id);

-- Лог запусков
CREATE TABLE runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    models_planned  INT,
    models_done     INT,
    cars_fetched    INT,
    cars_failed     INT,
    error_log       JSONB
);
```

**Решения:**
- `encar_id` — естественный PK, синтетический не нужен
- `car_model_matches` — отдельная таблица, чтобы одно авто жило в нескольких моделях без дублей
- `raw_data` JSONB — «чёрный ящик» на случай, если encar поменяет структуру
- `search_models.encar_action` — разобранный JSON, чтобы собирать URL программно
- Цены в KRW (без пересчёта) — пересчёт делается на стороне сайта

---

## 4. Поля карточки и правила перевода

**Парсим 18 полей + 1 служебный (encar_id):**

| # | Поле в БД | Тип | Пример | Правило |
|---|---|---|---|---|
| 1 | `encar_id` | int (PK) | `42131435` | — |
| 2 | `brand` | str | `BMW` | Оригинал |
| 3 | `model` | str | `X5 (G05)` | Оригинал |
| 4 | `year_month` | date | `2025-11-01` | Парсим KR «25년 11월» → `2025-11-01` |
| 5 | `mileage_km` | int | `4027` | Убираем запятые |
| 6 | `displacement_cc` | int | `2998` | — |
| 7 | `fuel_ru` / `fuel_original` | text | `Бензин` / `가솔린` | KO→RU словарь |
| 8 | `transmission_ru` / `transmission_orig` | text | `Автомат` / `오토` | KO→RU словарь |
| 9 | `body_type` | text | `SUV` | Оригинал |
| 10 | `color_ru` / `color_original` | text | `Чёрный` / `검정색` | KO→RU словарь |
| 11 | `seats` | int | `5` | Из «5인승» |
| 12 | `import_type_ru` | text | `Официальный` | KO→RU словарь |
| 13 | `manufacturer_warranty` | text | `BMW` | Оригинал |
| 14 | `liens_seizures` | text | `0/0` | Из «0건·0건» |
| 15 | `accident_records` | int | `376` | — |
| 16 | `plate_number` | text | `158바6820` | Оригинал |
| 17 | `price_krw` | bigint | `128500000` | Убираем запятые |
| 18 | `photo_urls` | JSONB | `[...]` | Список URL |
| 19 | `encar_detail_url` | text | `https://fem.encar.com/cars/detail/42131435` | — |

**Словари переводов (KR → RU):**
- Топливо: 가솔린 → Бензин, 디젤 → Дизель, 하이브리드 → Гибрид, 전기 → Электро, LPG/가스 → Газ
- КПП: 오토 → Автомат, 수동 → Механика, CVT → Вариатор, DCT → Робот
- Цвет: 검정색 → Чёрный, 흰색 → Белый, 회색 → Серый, 은색 → Серебристый, 파란색 → Синий, 빨간색 → Красный
- Импорт: 정식수입 → Официальный, 병행수입 → Параллельный

**Исключено:**
- ❌ Мощность (л.с.) — нет на encar
- ❌ Регион (지역) — не нужен

---

## 5. Конфиг моделей (models.yaml)

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
    fuel: gasoline              # опционально
    transmission: automatic     # опционально
    body_type: SUV              # опционально
    price_from: 30000000        # KRW, опционально
    price_to: 200000000         # KRW, опционально
    sort: ModifiedDate          # ModifiedDate | PriceAsc | PriceDesc | MileageAsc

  - slug: kia-sportage-nq5
    name: "Kia Sportage (NQ5)"
    enabled: true
    priority: 20
    manufacturer: Kia
    model_group: Sportage
    model: "Sportage (NQ5)"
    year_from: 2022
    fuel: hybrid
```

**Модуль `encar_url.build_action(cfg)`** — собирает S-expression из полей:

```python
def build_action(cfg: ModelConfig) -> dict:
    parts = ["And", "Hidden.N", "CarType.N"]
    if cfg.manufacturer:
        parts.append(f"Manufacturer.{cfg.manufacturer}")
    if cfg.model_group:
        parts.append(f"ModelGroup.{cfg.model_group}")
    if cfg.model:
        parts.append(f"Model.{quote(cfg.model)}")
    action = "(" + "._.".join(parts) + ".)"
    return {
        "action": action,
        "toggle": {"5": 1},
        "layer": "",
        "sort": cfg.sort or "ModifiedDate",
        "page": 1,
        "limit": 20,
        "searchKey": "",
        "loginCheck": False,
    }
```

**`sync`** — синхронизирует YAML с БД:
- Новые slug → INSERT
- Отсутствующие в YAML → `enabled=false` (история сохраняется)
- Изменился `encar_action` → UPDATE

---

## 6. Планировщик и защита от бана

**3-дневная ротация:**
```python
def models_for_today(today: date) -> list[SearchModel]:
    bucket = (today.isoweekday() - 1) % 3
    all_models = repo.get_enabled_models()  # сортировка по priority, slug
    return [m for i, m in enumerate(all_models) if i % 3 == bucket]
```

**Защита от бана (без прокси):**

| Приём | Значение |
|---|---|
| User-Agent | Ротация из 10+ реальных (Chrome/Safari/Edge) |
| Задержка между запросами | `random.uniform(2.0, 5.0)` сек |
| Задержка между моделями | `random.uniform(5.0, 15.0)` сек |
| Accept-Language | `ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7` |
| Referer | `https://www.encar.com/fc/fc_carsearchlist.do` |
| Куки | Один раз через Playwright, переиспользуем в httpx |
| Retry при 429 | 3 попытки с экспонентой 5/15/45 сек |
| Лимит запросов/час | ~1200 (естественно получается при задержках) |

**Cron:**
```cron
0 3 * * * cd /app && /usr/local/bin/python -m encar_parser run >> /var/log/encar.log 2>&1
```

---

## 7. Обработка ошибок

**Иерархия исключений:**
```python
class EncarError(Exception): pass
class EncarHTTPError(EncarError):
    def __init__(self, status: int, url: str): ...
class EncarBlocked(EncarError): pass
class EncarParseError(EncarError): pass
class EncarTimeout(EncarError): pass
class ModelError(EncarError): pass
class CarError(EncarError): pass
```

**Стратегия ретраев (tenacity):**
- 3 попытки, экспонента 5–60 сек
- Только для `429 / 5xx / Timeout`
- Не ретраим `403/404` (это окончательные ответы)

**Реакции на разные ошибки:**

| Ошибка | Реакция |
|---|---|
| `EncarBlocked` (капча) | Прервать модель, следующая модель |
| `EncarHTTPError(429)` | 3 ретрая с экспонентой |
| `EncarHTTPError(404)` | Пропустить авто, продолжить модель |
| `EncarParseError` | Пропустить авто, `raw_data` в БД |
| 3+ ошибок подряд в одной модели | `search_models.enabled = false` |
| Постгрес недоступен | Fail-fast, Docker перезапустит |

**Алерты (без внешних сервисов):**
- Ошибки пишутся в `runs.error_log` (JSONB) + `/var/log/encar_alerts.json`
- Внешние уведомления — за пределами MVP

---

## 8. Тестирование

**Пирамида:**
```
   E2E (live)         2-3 теста, помечены @pytest.mark.live
   Интеграционные     5-10 тестов с фикстурами encar_*.json
   Юнит-тесты         20+ тестов чистых функций
```

**Структура `tests/`:**
```
tests/
├── conftest.py
├── fixtures/
│   ├── search_list_bmw_x5.json
│   ├── car_detail_42131435.json
│   └── car_detail_blocked.html
├── unit/
│   ├── test_encar_url.py        # build_action()
│   ├── test_translations.py     # KO → RU
│   ├── test_parsers.py          # JSON → Car
│   ├── test_repository.py       # upsert, idempotency
│   └── test_scheduler.py        # 3-day rotation
├── integration/
│   ├── test_pipeline_api.py
│   ├── test_pipeline_browser.py
│   └── test_retry.py
└── e2e/
    └── test_smoke_live.py
```

**Покрытие:** целимся в **≥80%** для `parsers/`, `encar_url.py`, `translations.py`, `repository.py`.

**Команды:**
```bash
make test              # юнит + интеграционные
make test-live         # + E2E на реальном encar
```

**Где брать фикстуры:** записать реальные ответы encar в `tests/fixtures/` (один раз) и закоммитить в git — тесты работают офлайн.

---

## 9. Деплой

**Docker Compose (2 сервиса):**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: encar
      POSTGRES_USER: encar
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
  parser:
    build: .
    depends_on: [postgres]
    command: ["sh", "-c", "python -m encar_parser migrate && cron -f"]
    env_file: .env
    volumes:
      - ./models.yaml:/app/models.yaml:ro
volumes:
  pgdata:
```

Запуск: `docker compose up -d` → крутится вечно, раз в сутки парсит свою треть моделей.

---

## 10. Прокси (будущее)

Когда `10 000/запуск` станет тесно, добавляется `ProxyFetcher`:
- Абстракция `Fetcher` уже это позволяет
- Подключение прокси-пула — замена фабрики, не переписывание
- Оценка: ~30 минут работы

---

## 11. Открытые вопросы / допущения

- **Структура action S-expression** — взята из примера URL, нужно подтвердить, что формат стабильный (encar редко меняет URL-схему)
- **Прямые API-эндпоинки encar** — точные URL будут определены при первой реализации fetchers/api.py (вероятно, что-то вроде `https://api.encar.com/search/car/list/general`)
- **«Записи ДТП» = 376** — на скрине цифра показалась неожично большой, возможно это не «records», а другая метрика. Уточнить в первой партии парсинга
- **«짐 = 3»** — на скрине поле называется «짐» (корейское «багаж»/«вещь»), назначение пока непонятно. Уточнить

---

## 12. Что в MVP, что нет

**В MVP (после writing-plans):**
- ✅ Все модули из п. 2
- ✅ Схема БД из п. 3
- ✅ Все 18 полей карточки
- ✅ 1-3 примера моделей в `models.yaml`
- ✅ Юнит-тесты + 1 интеграционный
- ✅ Docker Compose
- ✅ E2E-smoke

**Не в MVP (отложено):**
- ❌ Прокси (п. 10)
- ❌ 100+ моделей (добавляются постепенно, не в коде парсера)
- ❌ Внешние уведомления (Telegram/почта)
- ❌ Админка для моделей
- ❌ Дашборд / веб-интерфейс

---

**Готов к writing-plans.**
