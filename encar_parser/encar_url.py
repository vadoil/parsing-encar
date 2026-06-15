"""Build encar.com search URLs from a ModelConfig."""

from __future__ import annotations

import json
import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field

EncCarType = Literal["for", "new", "domestic"]  # used/foreign-new/domestic
SortOrder = Literal["ModifiedDate", "PriceAsc", "PriceDesc", "MileageAsc", "YearDesc"]


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

    fuel: str | None = None  # gasoline, diesel, hybrid, electric, lpg
    transmission: str | None = None  # automatic, manual, cvt
    body_type: str | None = None  # sedan, suv, etc.

    price_from: int | None = None
    price_to: int | None = None
    mileage_to: int | None = None

    car_type: EncCarType = "for"
    sort: SortOrder = "ModifiedDate"
    limit: int = Field(default=20, ge=1, le=100)


def _escape(value: str) -> str:
    """Escape value for the S-expression filter string."""
    return value.replace(" ", "%20").replace("(", "_").replace(")", "_")


def _year_range_clause(cfg: ModelConfig) -> str | None:
    if cfg.year_from is None and cfg.year_to is None:
        return None
    parts = []
    if cfg.year_from is not None:
        parts.append(f"YearFrom.{cfg.year_from}")
    if cfg.year_to is not None:
        parts.append(f"YearTo.{cfg.year_to}")
    return "(._.".join(parts) + ".)"


def build_action(cfg: ModelConfig) -> dict:
    """Build the action JSON dict that encar expects in the URL hash.

    The action is an S-expression-style filter:
    (And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.(C.ModelGroup.X5._.Model.X5%20(G05_).))))
    """
    parts: list[str] = ["And", "Hidden.N", "CarType.N"]

    if cfg.manufacturer:
        parts.append(f"Manufacturer.{_escape(cfg.manufacturer)}")
    if cfg.model_group:
        parts.append(f"ModelGroup.{_escape(cfg.model_group)}")
    if cfg.model:
        parts.append(f"Model.{_escape(cfg.model)}")
    if cfg.fuel:
        parts.append(f"Fuel.{_escape(cfg.fuel)}")
    if cfg.transmission:
        parts.append(f"Transmission.{_escape(cfg.transmission)}")
    if cfg.body_type:
        parts.append(f"BodyType.{_escape(cfg.body_type)}")

    year_clause = _year_range_clause(cfg)
    if year_clause:
        parts.append(year_clause)

    action_str = "(" + "._.".join(parts) + ".)"

    return {
        "action": action_str,
        "toggle": {"5": 1},
        "layer": "",
        "sort": cfg.sort,
        "page": 1,
        "limit": cfg.limit,
        "searchKey": "",
        "loginCheck": False,
    }


def build_url(cfg: ModelConfig) -> str:
    """Build a full encar.com search URL for the given config."""
    action = build_action(cfg)
    hash_payload = json.dumps(action, ensure_ascii=False, separators=(",", ":"))
    encoded_hash = urllib.parse.quote(hash_payload, safe="")
    return f"https://www.encar.com/fc/fc_carsearchlist.do?carType={cfg.car_type}#!{encoded_hash}"
