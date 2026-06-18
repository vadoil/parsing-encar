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
