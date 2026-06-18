# Что изменено: переключение парсера на реальный API encar

## Суть проблемы (было)
Код строил ссылку на **фронтенд-страницу** encar
(`www.encar.com/fc/fc_carsearchlist.do#!{json}` и `fem.encar.com/cars/detail/{id}`),
а парсеры ждали **JSON от внутреннего API**. Часть после `#!` вообще не уходит
на сервер — поэтому «по API» ничего не работало: фетчер получал HTML, а
`resp.json()` падал. Тесты были зелёными только потому, что парсеру подавали
заранее собранный JSON правильной формы.

## Что сделано (стало)
1. **`encar_parser/encar_url.py`** — переписан под реальный API:
   - `build_q(cfg)` — фильтр-выражение `q` в формате encar:
     `(And.(C.CarType.Y._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5 (G05).))))`
   - `build_sr(cfg, page)` — сортировка+пагинация: `|ModifiedDate|0|20`
   - `build_list_api_url(cfg)` → `https://api.encar.com/search/car/list/general?count=true&q=...&sr=...`
   - `build_url()` теперь возвращает **этот API-URL** (его и дёргает pipeline).
   - Поля `raw_q` и `car_type_code` в `ModelConfig` — ручное переопределение.
2. **`parsers/list_page.py`** — читает настоящую форму ответа
   (`SearchResults` — массив верхнего уровня + `Count`), со старой формой как fallback.
   Добавлен `parse_search_list_result()` (отдаёт ещё и total для пагинации).
3. **`config.py`** — добавлены `api_list_base`, `api_detail_template`, `encar_referer`.
4. **`cli.py`** — detail-запросы идут на реальный `api.encar.com/v1/readside/vehicle/{id}`;
   добавлена команда **`probe`** для проверки живого API.
5. **`fetchers/api.py`** — дефолтный `Referer`/`Origin` (encar часто требует их, иначе 403).
6. Тесты обновлены под новый контракт.

## Как проверить (локально, где есть сеть и зависимости)
```bash
uv sync                 # поставить зависимости
uv run pytest -q        # тесты должны пройти
uv run encar-parser probe bmw-x5-g05          # дёрнуть живой список по модели
uv run encar-parser probe bmw-x5-g05 --detail-id 42131435   # + одна карточка
```
`probe` напечатает реальный URL, `Count`, первые распарсенные элементы и
top-level ключи ответа.

## Что ещё нужно сверить вживую (я не мог проверить без сети)
- **`car_type_code`** внутри `q` (Y/N): если список приходит пустым — подбери код
  или вставь готовый `q` из DevTools браузера в `raw_q` (см. пример в models.yaml).
- **Форма ответа detail-эндпоинта** `/v1/readside/vehicle/{id}`: парсер
  `parsers/details.py` сейчас рассчитан на ключ `car.*`. Запусти
  `probe ... --detail-id <id>`, посмотри реальные имена полей и, если надо,
  поправь маппинг в `details.py` (скинь мне вывод — допишу точно).

---

## 2026-06-18 — VPS-деплой + фикс `build_q`

### Поднято на VPS `185.28.175.75` (Miami, US)
- Ubuntu 24.04 → apt-источники переключены с `ru.archive.ubuntu.com` на `archive.ubuntu.com`
  (зеркала залочены, но IP реально в US — `ipinfo.io` подтверждает Miami, `AS49791 Newserverlife LLC`).
- Установлены `docker.io` 29.1.3 + `docker-compose-v2` 2.40.3.
- Проект скопирован в `/opt/encar/` через `rsync` (исключая `.venv`, `.git`, `output/_raw*`, `output/_details.json`, `output/encar_export.*`).
- Сгенерирован случайный DB-пароль через `openssl rand -hex 24`, положен в `/opt/encar/.env` (`chmod 600`).
- Применены Alembic-миграции → 4 таблицы (`search_models`, `cars`, `car_model_matches`, `runs`).
- `python -m encar_parser sync` → 3 модели в БД.
- `docker compose up -d` → 2 контейнера: `encar-parser-1` (Up), `encar-postgres-1` (healthy).
- `probe bmw-x5-g05` → **`Count=1105`** ✅ (raw_q из models.yaml работает).

### Найден и исправлен латентный баг `build_q`
Генератор `q`-выражений выдавал невалидный S-expression — `Hidden.N._.`
не вставлялся, `Year.range` отсутствовал, и глубина вложенности была лишней
(4 уровня вместо 3). BMW работал только потому, что у него был захваченный
`raw_q`; **Kia и Hyundai на VPS возвращали `HTTP 400`**.

**Что починено** (коммит `9e07dad`):
- `encar_parser/encar_url.py::build_q` — переписан под канонический формат
  `(And.Hidden.N._.(C.CarType.<code>._.(C.Manufacturer.<m>._.ModelGroup.<g>.))
   _.Year.range(<YYYYMM>..<YYYYMM>).)`.
  - **Wrapper**-клетки получают префикс `C.`, **body** (самая глубокая) — голая.
  - `Year.range` добавляется только когда заданы **оба** `year_from` и `year_to`,
    в формате 6-значного `YYYYMM` (например `201800..202699` = январь 2018 — декабрь 2026).
  - Поле `model` в `ModelConfig` больше **не используется** в `q` (только для справки в БД).
- `tests/unit/test_encar_url.py`:
  - **golden-тест** `test_build_q_golden_bmw_matches_raw_q` — `build_q(bmw_config) == raw_q из DevTools`.
    Это и есть спецификация формата.
  - `test_build_q_with_year_range` / `test_build_q_no_year_range_when_only_one_bound`.
  - Старый `test_build_q_with_model_nests_deepest` переименован и переписан —
    `model` в конфиге теперь метаданные, не часть `q`.
- **62 теста зелёные** (раньше 59; +3 новых).
- Mypy чисто; ruff чисто на изменённых строках (один пре-existing `PT018` в
  `test_build_action_reference_payload` оставлен — правило «не ослаблять тесты»).

### Безопасность
- Root-пароль VPS был отправлен в чате и сохранён в логе сессии — **рекомендуется сменить**
  и поставить SSH-ключ с отключением `PasswordAuthentication`.

---

## 2026-06-18 — Tasks 2-5 из совета: пагинация, ретраи, last_seen_at, мелочи

### Task 2 — пагинация в `pipeline.run_model` (коммит `24459f3`)
- Раньше бралась только первая страница → для BMW (Count=1103) теряли 1083 авто.
- Теперь цикл `1..max_pages` (default 10), стоп на: пустой странице, короткой странице, `total >= Count`, или `max_pages`.
- Новый `make_list_url_for_page(encar_action, page)` строит URL с правильным offset через `parse_qsl` (избегаем бага `parse_qs+urlencode` который сериализовал `key=['v']`).
- 3 integration-теста + 5 unit-тестов на URL-билдер. **+8 тестов** (62→70).
- Реальная проверка на VPS: BMW 1103 машин, все запросы пагинируются корректно.

### Task 3 — tenacity ретраи в `ApiFetcher` (коммит `51ab8cc`)
- Tenacity подключён через `AsyncRetrying` + `wait_random_exponential`.
- `RetryableError(FetcherError)` — подкласс, который триггерит retry. Plain `FetcherError` (403, 4xx) — НЕ триггерит, сразу летит в `FallbackFetcher`.
- Retry: 429, 5xx, `TimeoutException`, `ConnectError`. Up to `retry_max_attempts=3` с экспонентой + джиттер 5..60 сек.
- Hard 403 (бот-блок) — без ретраев, сразу FetcherError → Playwright fallback.
- 9 тестов: 429→200, constant 429 (raise после 3 попыток), 403 (no retry), 5xx→raise, 5xx→200, timeout→raise, timeout→200, 404 (no retry), ConnectError (retry).
- Tests используют zero-wait фикстуру → 0.23 сек на 9 тестов.
- **+9 тестов** (70→79).

### Task 4 — `last_seen_at` на каждый upsert (коммит `1915eaf`)
- Раньше `last_seen_at` ставился только на UPDATE, INSERT оставлял NULL.
- Это ломало детект «проданных» авто: первый прогон давал NULL, второй — обновлял. Запрос `last_seen_at < N days ago` был ненадёжным до второго прогона.
- Теперь `upsert_car` ставит `last_seen_at = now()` на ОБА бранча (insert и update). `now` берётся один раз в начале функции, чтобы insert и immediate-read видели одинаковое время.
- 1 новый тест: `test_upsert_car_updates_last_seen_at_on_each_call` (insert → sleep 10ms → re-upsert → assert second > first).
- Существующий `test_upsert_car_creates` усилен проверкой `last_seen_at is not None`.
- **+1 тест** (79→80).

### Task 5 — мелочи (коммит `3190d42`)
- `pyproject.toml`: `addopts = "-v --strict-markers -m 'not live'"`. Live-тесты скипаются по умолчанию; `pytest -m live` для opt-in (CLI -m выигрывает у addopts).
- `types-PyYAML>=6.0` в dev-deps → mypy больше не ругается на yaml-стабы.
- `mypy encar_parser/` → **0 ошибок** (было 3).
- `ruff check --fix` применён по всему проекту: убраны неиспользуемые импорты, sorted imports, `X | Y` вместо `Union`, `datetime.UTC` вместо `timezone.utc`, `collections.abc.Sequence` и т.п. **42 auto-fixable исправлены**.
- 5 пре-existing ruff issues (B008 typer.Option, N806 Session, B904) — оставлены (требуют code restructure, не в скоупе «не ослаблять»).
- 21 файл в коммите (включая авто-фиксы в 12 test/alembic файлах).

### Итог по советам 1-5
| Task | Что | Тестов | Live-проверка |
|---|---|---|---|
| 1 | `build_q` grammar fix | +3 (golden) | ✅ BMW 1103, Kia/Hyundai 200 OK |
| 2 | Pagination (max_pages) | +8 (3 integration + 5 unit) | ✅ build OK, manual verify deferred |
| 3 | Tenacity retries | +9 (zero-wait fixture) | ⏳ runtime wait=5..60s |
| 4 | `last_seen_at` always | +1 (existing test strengthened) | ⏳ needs scheduled cron run |
| 5 | Dev-ex polish | 0 (no new) | ✅ mypy 0, ruff --fix applied |

**Tests: 80 passed, 2 deselected (live). mypy: clean (24 source files).**

## Фаза 0 — карта полей encar API (коммит docs/encar/field-map.md)

**Цель фазы:** перед большим бэкафиллом зафиксировать РЕАЛЬНУЮ структуру
ответа `api.encar.com/v1/readside/vehicle/{id}` на ground-truth из
`output/_details.json` (30 BMW X5 G05). Без правок кода.

### Что найдено (важные расхождения со спекой)

1. **`condition.insurance` = `null` для всех 30 машин.** Гипотеза Фазы 1
   «реальные ДТП в `condition.insurance`» — **неверна**: encar НЕ возвращает
   страховую историю в этом endpoint. Запланированные колонки
   `insurance_accident_my/other`, `insurance_total_loss`, `insurance_flood`,
   `insurance_theft`, `owner_changes` заполнять нечем.

2. **`category.{driveType, transmission, fuel, origin, vehicleType}` = `null`**
   для всех 30. Эти поля НЕ возвращаются API — добавлять их в схему нельзя.
   Drive type / transmission / fuel / origin — брать из других путей
   (см. field-map.md).

3. **`condition.accident.recordView` = 29/30 (96.7%)** — это НЕ «было ДТП».
   По структуре это флаг «отчёт истории доступен к просмотру». Real accident
   data — только в свободном тексте `contents.text` («무사고», «전손», «침수»).

4. **Дубль 41811360/41814518 — одна и та же машина**, `vehicleId=41811360`
   в обоих. JSON-ключи не каноничны, для dedup — `vehicleId` int
   (а VIN/photo Jaccard — fallback).

5. **`category.gradeName` = реальные трим-имена** (`'xDrive 30d M 스포츠'` и т.п.)
   — это **первичный источник для engine_code** при матчинге с HP-каталогом.

6. **`category.originPrice`** — оригинальная цена нового в 만원 (13050 = 130.5M KRW).
   Доступна для всех 30 машин. Раньше не использовалась.

7. **`category.importType`** — `'REGULAR_IMPORT' × 27, 'NONE_IMPORT_TYPE' × 3`.
   Не `'PARALLEL_IMPORT'`, как предполагала `translations.py` — нужно проверить
   покрытие.

8. **`warranty.companyName` — 14 distinct значений**, включая 5 вариантов BMW
   (`'BMW'`, `'BMW 코리아'`, `'BMW코리아'`, `'bmw코리아'`, `'비엠더블유 코리아'`)
   и аномалию `'가능' / '불가능'` (строка в объектном поле). Нужен
   нормализатор + флаг `warranty_anomaly`.

### Файлы
- `docs/encar/field-map.md` (новый) — карта всех ~100 полей с типами,
  примерами, distinct-счётчиками, пометками «не возвращается» / «нужна нормализация».
- `CHANGES_RU.md` (эта секция).

**Tests: без изменений (правок кода не было). Phase 1 приостановлена —
требуется пересмотр из-за находки про `condition.insurance`.**

## Фаза 1 — честная семантика аварийности (коммит docs/encar + alembic 0002 + parsers/models)

**Проблема:** старая колонка `accident_records: int` (0/1) в `cars` вводила
в заблуждение — 29/30 BMW X5 G05 в ground-truth имели значение 1, что
создавало впечатление «96.7% машин с ДТП». На самом деле
`condition.accident.recordView` — это флаг «отчёт истории доступен»,
а не счётчик аварий.

**Гипотеза «реальные ДТП в `condition.insurance`» — неверна:** insurance
в этом endpoint всегда `null`. API Encar просто не возвращает страховую
историю через `/v1/readside/vehicle/{id}`.

### Что сделано (минимальная версия — по согласованию)
1. **`CarData.accident_records: int | None`** → **`accident_report_available: bool | None`** в
   `encar_parser/parsers/details.py`. Legacy `accidentRecords: 376` (int) сворачивается в `True`,
   новый `recordView: bool` используется напрямую.
2. **`Car` model** в `encar_parser/db/models.py` — тип `Integer` → `Boolean`.
3. **Alembic миграция `0002_accident_report_available.py`** — rename column
   + cast Integer→Boolean с приведением `0 → False`, `!=0 → True`, NULL остаётся NULL.
4. **`pipeline.py`** — `upsert_car` использует новое имя.
5. **`output/build_export.py`** — CSV-колонка `accident_record` → `accident_report_available`.
6. **Тесты** — 3 новых на фикстурах из `output/_details.json`:
   - `test_real_recordview_true_yields_report_available_true` (29/30 → True)
   - `test_real_recordview_false_yields_report_available_false` (1/30 → False)
   - `test_real_condition_insurance_is_null_in_all_samples` (regression guard)
   Плюс обновлены 2 существующих теста: `test_parse_car_detail_full`
   (`accident_records == 376` → `accident_report_available is True`) и
   `test_parse_car_detail_real_api_shape` (аналогично).

### Что НЕ сделано (намеренно)
- **Не добавлены колонки** `insurance_accident_my/other`, `insurance_total_loss`,
  `insurance_flood`, `insurance_theft`, `owner_changes` — нет источника данных
  в API. Зафиксировано в `encar-open-questions.md` (2026-06-18).

### Приёмка
- На 30 BMW X5: 29 → `accident_report_available=True`, 1 → `False` (ID 40690603).
  Поле честно описывает доступность отчёта, а не аварийность.
- `uv run pytest -m "not live"`: **125 passed, 2 deselected** (+3 новых).
- `uv run ruff check .`: 46 ошибок (без изменений от baseline до Фазы 1).
- `uv run mypy encar_parser/`: 6 ошибок (без изменений от baseline).

## Тестовый прогон 2026-06-18 — 100 машин, 5 моделей

**Цель:** перед Фазой 2 проверить, что Phase 0/1 (поле `accident_report_available`,
новая миграция 0002) корректно работают на «свежих» данных, не только на
ground-truth из `output/_details.json`.

**Запуск:** `uv run python scripts/test_parse_5_models.py` (после `sync`
в локальный docker postgres). Параметры: `MAX_PAGES=1`, ~20 машин/модель.

### Результат
- **100 машин за 458 секунд** (≈4.6 сек/машина с rate-limit задержками),
  **0 ошибок**.
- 5 моделей × 20 машин = 100 (по 1 странице каждая):
  - Hyundai Avante (CN7), Genesis G80, Audi A6 (C8), BMW X5 (G05), Hyundai Palisade
- Все 100 машин заполнены полностью: `displacement_cc`, `body_type`, `color_ru`,
  `price_krw`, `photo_urls`, `accident_report_available`, `raw_data`.
- `accident_report_available=True` для 100/100 (сходится с 96.7% в BMW X5 sample
  из Phase 0 — на новой выборке ratio ещё выше, похоже это системный bias
  encar, см. Phase 0 finding).
- **Найден реальный cross-listing дубль** для Фазы 5: cars `42124439` и
  `42137295` — Audi A6 (C8), `year=2023-09`, `mileage=24 048`,
  `price=48 800 000`. Разные `encar_id`, но всё остальное идентично — это
  именно та дубликат-ситуация, под которую проектируется Фаза 5.

### Файлы
- `scripts/test_parse_5_models.py` (новый) — one-off скрипт, 5 моделей × 1 страница.
  Не CLI-команда (CLI `run` использует 3-дневную ротацию — не подходит для
  targeted-теста). Скрипт переиспользуем: можно повторять для приёмочной
  проверки после Фаз 2-6.

### Замечания
- Alembic 0002 первая попытка упала на `UPDATE cars SET accident_records = TRUE` —
  PostgreSQL строг к boolean/int cast. Фикс: добавил `_new` колонку, скопировал
  с приведением, дропнул старую. **Amendment Phase 1** (тот же коммит,
  `9163094`).
