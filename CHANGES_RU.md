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
