"""User-facing display name for car brands.

The DB and the live API use both English labels (e.g. ``"Hyundai"`` in
``catalog_test.xlsx``) and Korean transliterations (e.g. ``"현대"`` in the
``Manufacturer`` cell of the search query). For UI we want a single
consistent English display name — so the catalogue page never shows
``제네시스`` while a Hyundai shows ``Hyundai``.

This module is the single reverse-map. Same source of truth as
:mod:`encar_parser.car_type` (it imports from there). If you add a
brand to ``DOMESTIC_BRANDS_EN_TO_KR``, you don't need to touch
anything here — the reverse map is computed from the forward one.
"""
from __future__ import annotations

from encar_parser.car_type import DOMESTIC_BRANDS_EN_TO_KR


def brand_display(brand: str | None) -> str:
    """Return the English display name for ``brand``.

    * ``"현대"`` → ``"Hyundai"`` (reverse-lookup of the Korean
      transliteration from :data:`encar_parser.car_type.DOMESTIC_BRANDS_EN_TO_KR`).
    * ``"Hyundai"`` → ``"Hyundai"`` (English keys are returned as-is).
    * ``"BMW"``, ``"Mercedes-Benz"``, etc. → returned as-is (imports are
      already in English; we don't translate them).
    * ``None`` / empty string → returned as-is (the caller decides what
      to render).
    * Unknown brand → returned as-is (mirrors the policy in
      :mod:`encar_parser.car_type`: prefer visible defaults over
      silent fallbacks).
    """
    if not brand:
        return brand or ""
    # Build reverse once per call (the dict is tiny — 10 entries).
    reverse = {kr: en for en, kr in DOMESTIC_BRANDS_EN_TO_KR.items()}
    return reverse.get(brand, brand)
