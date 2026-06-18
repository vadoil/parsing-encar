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
