"""Single source of truth for Encar's CarType classification.

Background
----------
EncAr's search API has a CarType cell that splits the catalog in two:

* ``Y`` (국산 / domestic) — Korean brands: Hyundai, Kia, Genesis, KG Mobility
  (formerly Ssangyong), Renault Korea (formerly Samsung), GM Korea
  (Chevrolet), and any other brand headquartered in Korea.
* ``N`` (수입 / import) — every other brand (BMW, Mercedes-Benz, Audi,
  Porsche, Toyota, …).

If you ask Encar for BMW cars with CarType=Y, it returns 0 (Encar has no
domestic BMWs). Asking Hyundai with CarType=N also returns 0. So the
right cell must match the brand.

This module is the **only place** that knows the mapping. Two callers:

1. ``encar_parser/build_pool.py`` — at pool-generation time, when a
   ``(brand_label, family_label)`` pair from ``catalog_test.xlsx`` is
   expanded into a YAML model entry. The ``brand_label`` here is the
   *English* name from the catalog.
2. ``encar_parser/cli.py`` ``classify-brands`` command — for human
   review of the classification across all brands in ``models.yaml``.

The mapping covers both spellings (English catalog label + the Korean
``Manufacturer`` cell value used in the live API's search filter) so a
brand renamed across these two worlds (Ssangyong ↔ KG Mobility,
Samsung ↔ Renault Korea, Daewoo ↔ GM Korea) is recognised either way.

When a brand is neither in DOMESTIC_BRANDS_EN_TO_KR nor in
KNOWN_IMPORT_BRANDS we treat it as import (N) and log a warning — the
classifier never refuses to produce a CarType, but it makes the
default obvious.
"""
from __future__ import annotations

from encar_parser.utils.log import get_logger

log = get_logger(__name__)


# CarType codes used in the q-filter cell.
CAR_TYPE_DOMESTIC = "Y"
CAR_TYPE_IMPORT = "N"


# Domestic (국산) brands. Keys are the *English* brand labels used in
# catalog_test.xlsx (the human-curated reference). Values are the
# Korean transliterations Encar uses in the live API's Manufacturer cell.
# Edit this map to add/rename domestic brands.
DOMESTIC_BRANDS_EN_TO_KR: dict[str, str] = {
    "Hyundai": "현대",
    "Kia": "기아",
    "Genesis": "제네시스",
    "Ssangyong": "쌍용",          # renamed to KG Mobility in 2023
    "KG Mobility": "KG모빌리티",  # post-2023 rename
    "Samsung": "삼성",            # renamed to Renault Korea in 2023
    "Renault Korea": "르노코리아",  # post-2023 rename
    "GM Korea": "한국GM",
    "Chevrolet": "쉐보레",        # Korean-built only (imported Chevy = N)
    "Daewoo": "대우",              # historical, kept for old pool entries
}


# Korean transliterations of domestic brands — every spelling → "Y".
# Derived from DOMESTIC_BRANDS_EN_TO_KR so adding a brand to the dict
# above automatically extends this set.
_DOMESTIC_MANUFACTURERS: frozenset[str] = frozenset(DOMESTIC_BRANDS_EN_TO_KR.values())


# Known import (수입) brands. The classifier treats any brand on this
# list as a known import and emits NO warning for it (in contrast to
# unknown brands, which warn + default to "N").
KNOWN_IMPORT_BRANDS: frozenset[str] = frozenset({
    # English catalog labels
    "BMW", "Mercedes-Benz", "Audi", "Porsche", "Toyota", "Lexus",
    "Volkswagen", "Volvo", "Mini", "Bentley", "Rolls-Royce",
    "Ferrari", "Lamborghini", "Maserati", "McLaren", "Aston Martin",
    "Land Rover", "Jaguar", "Cadillac", "Lincoln", "Buick",
    "Ford", "Chrysler", "Jeep", "Dodge", "Ram", "Tesla",
    "Honda", "Nissan", "Mazda", "Subaru", "Mitsubishi", "Suzuki",
    "Daihatsu", "Infiniti", "Acura",
    "Alfa Romeo", "Fiat", "Peugeot", "Renault", "Citroen",
    "Skoda", "SEAT",
    # Korean transliterations observed in models.yaml
    "아우디",   # Audi
    "포르쉐",   # Porsche
    "인피니티", # Infiniti
    "재규어",   # Jaguar
})


def is_domestic_label(brand_label: str | None) -> bool:
    """Return True iff ``brand_label`` is a known domestic brand.

    ``brand_label`` may be either an English catalog name (e.g. ``"Hyundai"``)
    or the Korean manufacturer cell (e.g. ``"현대"``).
    Returns False for ``None``, empty string, or any unknown brand.
    """
    if not brand_label:
        return False
    return (
        brand_label in DOMESTIC_BRANDS_EN_TO_KR
        or brand_label in _DOMESTIC_MANUFACTURERS
    )


def is_known_brand(brand_label: str | None) -> bool:
    """Return True iff ``brand_label`` appears in either the domestic or import map.

    Use this to suppress the "unknown brand" warning for brands we
    know exist in the catalog (e.g. BMW is a known import — not unknown).
    """
    if not brand_label:
        return False
    return is_domestic_label(brand_label) or brand_label in KNOWN_IMPORT_BRANDS


def classify_brand(brand_label: str | None) -> tuple[str, bool]:
    """Return ``(car_type_code, was_domestic)`` for ``brand_label``.

    * ``car_type_code`` — ``"Y"`` for domestic, ``"N"`` for import.
    * ``was_domestic`` — True iff the brand is in the domestic map.
      False means either known import OR unknown (we don't distinguish
      between the two here — both get code "N").

    Known imports + truly unknown brands → default to "N" (import).
    Truly unknown brands additionally log a ``cartype_unknown_brand``
    warning so they're easy to spot.
    """
    if not brand_label:
        log.warning(
            "cartype_empty_brand",
            hint="model has no manufacturer; defaulting to N (import)",
        )
        return CAR_TYPE_IMPORT, False
    if brand_label in DOMESTIC_BRANDS_EN_TO_KR:
        return CAR_TYPE_DOMESTIC, True
    if brand_label in _DOMESTIC_MANUFACTURERS:
        return CAR_TYPE_DOMESTIC, True
    if brand_label not in KNOWN_IMPORT_BRANDS:
        log.warning(
            "cartype_unknown_brand",
            brand=brand_label,
            hint="brand not in DOMESTIC_BRANDS_EN_TO_KR or KNOWN_IMPORT_BRANDS; "
                 "defaulting to N (import). If this brand is actually Korean (국산), "
                 "add it to encar_parser.car_type.DOMESTIC_BRANDS_EN_TO_KR.",
        )
    return CAR_TYPE_IMPORT, False


def known_brands() -> tuple[list[str], list[str]]:
    """Return sorted (domestic, import) brand lists (deduplicated)."""
    domestic = sorted(set(DOMESTIC_BRANDS_EN_TO_KR.keys()) | _DOMESTIC_MANUFACTURERS)
    import_ = sorted(KNOWN_IMPORT_BRANDS - (set(DOMESTIC_BRANDS_EN_TO_KR) | _DOMESTIC_MANUFACTURERS))
    return domestic, import_
