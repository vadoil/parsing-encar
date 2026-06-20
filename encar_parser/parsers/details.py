"""Parse the JSON car detail response from encar.

Two shapes are supported:

1. Legacy / mock (kept for tests and older callers)::

       {"car": {"vehicleNo": "158바6820", "mileage": "4,027", ...}}

2. Real api.encar.com shape (top-level flat with sections)::

       {"category": {...}, "spec": {...}, "advertisement": {...},
        "condition": {...}, "photos": [{"path": "..."}], ...}
"""

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
    # True if a vehicle history report is available for this listing on the
    # Encar page (from `condition.accident.recordView`). NOT an accident count.
    # Encar's API does not expose the actual insurance history through this
    # endpoint — `condition.insurance` is null. For real accident history
    # the listing page must be scraped (see encar-open-questions.md).
    accident_report_available: bool | None = None
    plate_number: str | None = None
    price_krw: int | None = None
    photo_urls: list[str] = field(default_factory=list)
    encar_detail_url: str = ""
    raw_data: dict[str, Any] | None = None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):  # bool is a subclass of int; treat as not-a-number
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def _parse_year_month(value: Any) -> date | None:
    """Parse KR year like '25년 11월', ISO '2025-11', or YYYYMM '202511'."""
    if not value:
        return None
    s = str(value)
    # Compact YYYYMM (e.g. "202511" from the real API)
    m = re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1)
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


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _parse_legacy(payload: dict, encar_id: int, brand: str, model: str) -> CarData:
    """Parse the legacy `car.*` shape."""
    car = _as_dict(_nested(payload, "car"))

    fuel_orig = _nested(car, "fuel", "name")
    trans_orig = _nested(car, "transmission", "name")
    color_orig = _nested(car, "color", "name")
    import_orig = _nested(car, "importType", "name")

    liens = _nested(car, "liens", default="")
    seizures = _nested(car, "seizures", default="")
    liens_seizures: str | None = None
    if liens or seizures:
        liens_seizures = f"{liens or '0건'}·{seizures or '0건'}"

    legacy_count = _to_int(car.get("accidentRecords"))

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
        # Legacy shape stored an integer count; collapse to bool for the new
        # field. Any non-zero count means "a record exists".
        accident_report_available=(legacy_count > 0) if legacy_count is not None else None,
        plate_number=car.get("vehicleNo"),
        price_krw=_to_int(car.get("price")),
        photo_urls=[str(p) for p in photos if isinstance(p, str)],
        encar_detail_url=f"https://fem.encar.com/cars/detail/{encar_id}",
        raw_data=car or None,
    )


def _parse_real(payload: dict, encar_id: int, brand: str, model: str) -> CarData:
    """Parse the real api.encar.com /v1/readside/vehicle/{id} shape.

    Sections in the live response: manage, category, advertisement, contact,
    spec, photos, options, condition, partnership, contents, view, and a few
    top-level scalars (vehicleId, vin, vehicleNo).
    """
    category = _as_dict(payload.get("category"))
    spec = _as_dict(payload.get("spec"))
    advertisement = _as_dict(payload.get("advertisement"))
    condition = _as_dict(payload.get("condition"))
    warranty = _as_dict(category.get("warranty"))

    fuel_orig = spec.get("fuelName")
    trans_orig = spec.get("transmissionName")
    color_orig = spec.get("colorName")
    import_orig = category.get("importType")  # e.g. "REGULAR_IMPORT"

    seizing = _as_dict(condition.get("seizing"))
    seizing_count = _to_int(seizing.get("seizingCount")) or 0
    pledge_count = _to_int(seizing.get("pledgeCount")) or 0
    liens_seizures = f"{pledge_count}건·{seizing_count}건"

    accident = _as_dict(condition.get("accident"))
    # recordView=True means "a vehicle history report is available on the
    # listing page". It is NOT an accident count — Encar's API does not
    # expose real insurance history in this endpoint. The field was renamed
    # in Phase 1 to reflect its actual semantics. See encar-open-questions.md.
    accident_report_available = bool(accident.get("recordView"))

    photos = payload.get("photos") or []
    if not isinstance(photos, list):
        photos = []
    photo_urls: list[str] = []
    for p in photos:
        if isinstance(p, dict) and p.get("path"):
            path = str(p["path"])
            # Paths come as /carpicture03/pic4213/42131435_001.jpg — make absolute.
            # Hosted on ci.encar.com (img.encar.com is filtered / unreachable from
            # some networks; ci.encar.com is the working CDN for photo binaries).
            photo_urls.append(
                path if path.startswith("http") else f"https://ci.encar.com{path}"
            )

    # advertisement.price is in 만원 (10,000 KRW). Convert to KRW.
    price_man = _to_int(advertisement.get("price"))
    price_krw = price_man * 10000 if price_man is not None else None

    plate = payload.get("vehicleNo") or category.get("vehicleNo")

    return CarData(
        encar_id=encar_id,
        brand=brand or category.get("manufacturerName", ""),
        model=model or category.get("modelName", ""),
        year_month=_parse_year_month(category.get("yearMonth")),
        mileage_km=_to_int(spec.get("mileage")),
        displacement_cc=_to_int(spec.get("displacement")),
        fuel_ru=translate_fuel(fuel_orig) if fuel_orig else None,
        fuel_original=fuel_orig,
        transmission_ru=translate_transmission(trans_orig) if trans_orig else None,
        transmission_orig=trans_orig,
        body_type=spec.get("bodyName"),
        color_ru=translate_color(color_orig) if color_orig else None,
        color_original=color_orig,
        seats=_to_int(spec.get("seatCount")),
        import_type_ru=translate_import_type(import_orig) if import_orig else None,
        manufacturer_warranty=warranty.get("companyName"),
        liens_seizures=liens_seizures,
        accident_report_available=accident_report_available,
        plate_number=plate,
        price_krw=price_krw,
        photo_urls=photo_urls,
        encar_detail_url=f"https://fem.encar.com/cars/detail/{encar_id}",
        raw_data=payload if isinstance(payload, dict) else None,
    )


def parse_car_detail(
    *,
    encar_id: int,
    payload: Any,
    brand: str = "",
    model: str = "",
) -> CarData:
    """Parse a car detail JSON from encar.

    Dispatches to the real-API or legacy shape based on the payload structure.
    Brand and model are accepted as fallbacks (e.g. from the list parser).
    """
    if not isinstance(payload, dict):
        payload = {}
    if "car" in payload and isinstance(payload["car"], dict):
        return _parse_legacy(payload, encar_id, brand, model)
    return _parse_real(payload, encar_id, brand, model)
