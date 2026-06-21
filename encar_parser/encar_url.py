"""Build encar.com search requests from a ModelConfig.

Encar exposes an internal JSON API that its own front-end calls:

    https://api.encar.com/search/car/list/general?count=true&q=<filter>&sr=<sort>

`q` is an S-expression filter built from "category cells":

    (And.(C.CarType.Y._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5 (G05).))))

`sr` encodes sort + pagination:  |ModifiedDate|<offset>|<limit>

This module builds that API URL. It also keeps a human-readable front-end URL
(www.encar.com/...#!...) purely for reference / Referer headers.

CarType classification (Y/N) lives in :mod:`encar_parser.car_type`. Every
model in ``models.yaml`` carries its own ``car_type_code``; this module
falls back to ``"N"`` (import) when the field is missing — which is the
safer default since most unknown brands are imports, and asking Encar
for an import brand with CarType=Y silently returns 0 cars.

NOTE: encar can change CarType codes and field names. If a query returns 0
results, capture the real `q` from your browser devtools (Network tab) and paste
it into models.yaml as `raw_q:` — it overrides the generated filter.
"""

from __future__ import annotations

import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field

from encar_parser.car_type import CAR_TYPE_DOMESTIC, CAR_TYPE_IMPORT, classify_brand
from encar_parser.utils.log import get_logger

log = get_logger(__name__)

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
    # Encar CarType cell code used inside `q`. Default is ``"N"`` (import) —
    # the safer fallback for unknown brands, since most manufacturers in
    # the global market are imports. Set this explicitly in ``models.yaml``
    # (see :mod:`encar_parser.car_type` for the domestic/import split) so
    # the YAML is the source of truth, not this default.
    car_type_code: str = CAR_TYPE_IMPORT


def _cell(field: str, value: str) -> str:
    """A single category cell: C.<Field>.<Value>"""
    return f"C.{field}.{value}"


def build_q(cfg: ModelConfig) -> str:
    """Build the S-expression `q` filter for the encar API.

    Produces e.g.:
        (And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.ModelGroup.X5.))._.Year.range(201800..202699).)

    Structure (verified against the real API via DevTools):
      (And.
        Hidden.N._.                                   # fixed prefix
        (C.CarType.<code>._.(C.Manufacturer.<m>._.ModelGroup.<g>.))  # CarType cell
        ._.Year.range(YYYYMM..YYYYMM)                 # optional year range
      .)

    The `model` field on the config is metadata only — encar's search expression
    stops at ModelGroup level. The model name is stored on the Car record but
    does not appear in `q` (use a raw_q if you need finer granularity).
    """
    if cfg.raw_q:
        return cfg.raw_q

    # Sanity-check: if we know the brand, does the explicit car_type_code
    # match what we'd derive? Mismatch is almost always a typo in models.yaml
    # — Encar will silently return 0 cars if we ask for BMW with CarType.Y.
    if cfg.manufacturer:
        derived_code, recognised = classify_brand(cfg.manufacturer)
        if recognised and derived_code != cfg.car_type_code:
            log.warning(
                "cartype_mismatch_with_brand",
                slug=cfg.slug,
                manufacturer=cfg.manufacturer,
                configured=cfg.car_type_code,
                derived_from_brand=derived_code,
                hint="CarType in models.yaml disagrees with the brand's "
                     "known classification in encar_parser.car_type. "
                     "Update models.yaml or add the brand to "
                     "DOMESTIC_BRANDS_EN_TO_KR.",
            )

    # Cells from shallowest to deepest. The deepest becomes the body (bare, no
    # C. prefix); everything above is a wrapper (with C. prefix).
    fields: list[tuple[str, str]] = []
    if cfg.manufacturer:
        fields.append(("Manufacturer", cfg.manufacturer))
    if cfg.model_group:
        fields.append(("ModelGroup", cfg.model_group))

    if fields:
        last_field, last_val = fields[-1]
        body = f"{last_field}.{last_val}."
        # Wrap with each preceding field. Wrapper is `C.Field.Val._.`, body is
        # the trailing `Field.Val.` (bare, no parens).
        expr = body
        for field, val in reversed(fields[:-1]):
            expr = f"C.{field}.{val}._.{expr}"
        car_type_cell = f"(C.CarType.{cfg.car_type_code}._.({expr}))"
    else:
        # No manufacturer or model_group — degenerate CarType cell.
        car_type_cell = f"(C.CarType.{cfg.car_type_code}._.)"

    # Optional year range: encar uses 6-digit YYYYMM (201800 = Jan 2018).
    # The connector after a closing paren is `_.` (no leading dot).
    year_range = ""
    if cfg.year_from is not None and cfg.year_to is not None:
        year_range = f"_.Year.range({cfg.year_from * 100:06d}..{cfg.year_to * 100 + 99:06d})"

    return f"(And.Hidden.N._.{car_type_cell}{year_range}.)"


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
