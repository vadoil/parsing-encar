"""Main pipeline: run a single model end-to-end."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
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


def make_list_url_for_page(encar_action: dict, page: int) -> str:
    """Build a paginated list URL from a stored encar_action dict.

    encar_action must contain ``api_url`` (the base URL with q + page-1 sr) and
    ``sort`` + ``limit`` so we can rebuild sr with the right offset for any page.
    """
    base_url = encar_action["api_url"]
    sort = encar_action.get("sort", "ModifiedDate")
    limit = encar_action.get("limit", 20)
    offset = (max(page, 1) - 1) * limit
    # parse_qsl gives a flat list of (k, v) tuples; we rebuild the query keeping
    # every param except sr=, which we replace with the page-specific offset.
    # (parse_qs would return lists per key and urlencode would then serialize
    #  the list literal — `key=['v']` — which is wrong.)
    parsed = urllib.parse.urlparse(base_url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    new_pairs = [(k, v) for k, v in pairs if k != "sr"]
    new_pairs.append(("sr", f"|{sort}|{offset}|{limit}"))
    new_query = urllib.parse.urlencode(new_pairs, safe="()._", quote_via=urllib.parse.quote)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


async def _fetch_list_with_meta(fetcher: Fetcher, url: str) -> tuple[list[Any], int | None]:
    """Fetch the search list. Returns (items, total_count_or_None)."""
    resp = await fetcher.get(url)
    payload = resp.json()
    items = parse_search_list(payload)
    total: int | None = None
    if isinstance(payload, dict):
        raw_total = payload.get("Count")
        if isinstance(raw_total, int):
            total = raw_total
    return items, total


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


async def _process_items(
    fetcher: Fetcher,
    session: AsyncSession,
    search_model: SearchModel,
    items: list[Any],
    detail_url_template: str,
    request_delay: RandomDelay,
) -> int:
    """Fetch detail + upsert each item. Returns number of successful upserts."""
    fetched = 0
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
                accident_report_available=car_data.accident_report_available,
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
    return fetched


async def run_model(
    search_model: SearchModel,
    *,
    fetcher: Fetcher,
    session: AsyncSession,
    list_url_for_page: Callable[[int], str],
    detail_url_template: str,
    request_delay: RandomDelay | None = None,
    max_pages: int = 10,
) -> int:
    """Process one model: list all cars (paginated), fetch each, upsert into DB.

    Pages are pulled via ``list_url_for_page(page)`` until one of:
      - the API returns an empty page (graceful stop; encar caps depth)
      - the API returns a short page (< page_size items)
      - we have collected at least as many items as the reported ``Count``
      - we have exhausted ``max_pages`` (safety)

    Returns the number of cars successfully inserted/updated.
    """
    request_delay = request_delay or RandomDelay(0.01, 0.05)  # fast in tests
    fetched = 0
    total_collected = 0
    total_reported: int | None = None

    log.info(
        "model_start",
        slug=search_model.slug,
        name=search_model.name,
        max_pages=max_pages,
    )

    for page in range(1, max_pages + 1):
        url = list_url_for_page(page)
        try:
            items, count = await _fetch_list_with_meta(fetcher, url)
        except Exception as e:
            log.error("model_list_failed", slug=search_model.slug, page=page, error=str(e))
            raise

        # First page: capture the reported total. Subsequent pages can return a
        # different count (encar is not strictly consistent); keep the first.
        if page == 1:
            total_reported = count

        log.info(
            "model_page_ok",
            slug=search_model.slug,
            page=page,
            items=len(items),
            total_reported=total_reported,
        )

        if not items:
            # Graceful stop: encar caps results at ~1000 and returns empty past it.
            log.info("model_pagination_stop_empty", slug=search_model.slug, page=page)
            break

        total_collected += len(items)
        fetched += await _process_items(
            fetcher, session, search_model, items,
            detail_url_template, request_delay,
        )

        # Stop conditions:
        #   1. We have as many items as the API reported.
        #   2. The page was short — there are no more results.
        if total_reported is not None and total_collected >= total_reported:
            log.info(
                "model_pagination_stop_count",
                slug=search_model.slug,
                total=total_collected,
                reported=total_reported,
            )
            break
        if len(items) < _infer_page_size(items, search_model):
            log.info(
                "model_pagination_stop_short_page",
                slug=search_model.slug,
                page=page,
                got=len(items),
            )
            break
    else:
        # max_pages exhausted without breaking — log but don't error.
        log.warning(
            "model_pagination_max_pages_hit",
            slug=search_model.slug,
            max_pages=max_pages,
            total=total_collected,
            reported=total_reported,
        )

    search_model.last_run_at = datetime.now(UTC)
    await session.commit()
    log.info("model_done", slug=search_model.slug, fetched=fetched)
    return fetched


def _infer_page_size(items: list[Any], search_model: SearchModel) -> int:
    """Best-effort page size for short-page detection.

    We don't know the exact page size used by the builder, so use a conservative
    default. Callers can rely on Count-based stop instead.
    """
    return 20
