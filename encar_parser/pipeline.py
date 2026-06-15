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
