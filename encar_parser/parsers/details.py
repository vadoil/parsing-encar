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