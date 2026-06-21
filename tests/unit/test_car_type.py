"""Unit tests for the CarType classification + URL integration.

Covers:
- Brand → CarType (Y/N) for the canonical domestic + import brands.
- Korean transliterations of domestic brands resolve to Y.
- Unknown brands default to N (import) — that's the safer default
  because most manufacturers in the global market are imports.
- ``build_q`` injects the right CarType cell into the S-expression.
- The sanity-warning fires when a model carries a CarType that
  disagrees with the brand's known classification.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import structlog.testing

from encar_parser.car_type import (
    CAR_TYPE_DOMESTIC,
    CAR_TYPE_IMPORT,
    DOMESTIC_BRANDS_EN_TO_KR,
    classify_brand,
    is_domestic_label,
    known_brands,
)
from encar_parser.encar_url import ModelConfig, build_q, build_list_api_url


# ── is_domestic_label ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "brand",
    [
        "Hyundai", "Kia", "Genesis", "Ssangyong",
        "KG Mobility", "Samsung", "Renault Korea", "GM Korea",
        # Korean transliterations
        "현대", "기아", "제네시스", "쌍용", "KG모빌리티", "르노코리아",
        "한국GM",
    ],
)
def test_is_domestic_label_true_for_domestic_brands(brand):
    assert is_domestic_label(brand) is True, f"{brand!r} should be domestic"


@pytest.mark.parametrize(
    "brand",
    [
        "BMW", "Mercedes-Benz", "Audi", "Toyota", "Lexus", "Porsche",
        "Volkswagen", "Volvo", "Mini", "Bentley", "Rolls-Royce",
        "Ferrari", "Lamborghini", "Maserati", "Land Rover", "Jaguar",
        "Cadillac", "Lincoln", "Ford", "Chrysler", "Tesla", "Honda",
        "Nissan", "Mazda", "Subaru", "Mitsubishi", "Suzuki",
        # Korean transliterations of imports
        "아우디", "포르쉐", "인피니티", "재규어",
        # Edge cases
        None, "", "UnknownBrand",
    ],
)
def test_is_domestic_label_false_for_imports_and_unknown(brand):
    assert is_domestic_label(brand) is False, f"{brand!r} should NOT be domestic"


# ── classify_brand ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "brand,expected_code",
    [
        ("Hyundai", CAR_TYPE_DOMESTIC),
        ("Kia", CAR_TYPE_DOMESTIC),
        ("Genesis", CAR_TYPE_DOMESTIC),
        ("Ssangyong", CAR_TYPE_DOMESTIC),
        ("현대", CAR_TYPE_DOMESTIC),
        ("기아", CAR_TYPE_DOMESTIC),
        ("제네시스", CAR_TYPE_DOMESTIC),
        ("쌍용", CAR_TYPE_DOMESTIC),
        ("BMW", CAR_TYPE_IMPORT),
        ("Mercedes-Benz", CAR_TYPE_IMPORT),
        ("Audi", CAR_TYPE_IMPORT),
        ("Toyota", CAR_TYPE_IMPORT),
        ("Lexus", CAR_TYPE_IMPORT),
        ("Porsche", CAR_TYPE_IMPORT),
        ("아우디", CAR_TYPE_IMPORT),
        ("포르쉐", CAR_TYPE_IMPORT),
        ("UnknownBrandXYZ", CAR_TYPE_IMPORT),  # default = import
        (None, CAR_TYPE_IMPORT),                # None → import + warning
        ("", CAR_TYPE_IMPORT),                  # empty → import + warning
    ],
)
def test_classify_brand_returns_correct_code(brand, expected_code):
    code, _ = classify_brand(brand)
    assert code == expected_code


def test_classify_brand_marks_known_domestic_brands():
    """``was_domestic`` is True iff the brand appears in DOMESTIC_BRANDS_EN_TO_KR
    or its Korean transliteration set. Imports + unknowns both return False."""
    for brand in ("Hyundai", "Genesis", "현대", "제네시스"):
        _, was_domestic = classify_brand(brand)
        assert was_domestic is True, f"{brand!r} should be classified domestic"


def test_classify_brand_imports_and_unknowns_share_was_domestic_false():
    """Imports AND unknowns both return was_domestic=False — they're treated the
    same way (default to N), but a warning fires only for unknowns."""
    for brand in ("BMW", "Audi", "UnknownBrandXYZ"):
        _, was_domestic = classify_brand(brand)
        assert was_domestic is False, f"{brand!r} should NOT be classified domestic"


def test_classify_brand_logs_warning_for_unknown(caplog):
    with structlog.testing.capture_logs() as logs:
        code, _ = classify_brand("MysteryBrand")
    assert code == CAR_TYPE_IMPORT
    # The warning was emitted.
    assert any(
        e.get("event") == "cartype_unknown_brand" and e.get("brand") == "MysteryBrand"
        for e in logs
    )


def test_classify_brand_logs_warning_for_empty_brand():
    with structlog.testing.capture_logs() as logs:
        code, _ = classify_brand(None)
    assert code == CAR_TYPE_IMPORT
    assert any(e.get("event") == "cartype_empty_brand" for e in logs)


def test_classify_brand_does_not_warn_for_known_import():
    """Imports don't get the "unknown brand" warning — they're known, just not domestic."""
    with structlog.testing.capture_logs() as logs:
        code, _ = classify_brand("BMW")
    assert code == CAR_TYPE_IMPORT
    assert not any(
        e.get("event") == "cartype_unknown_brand" for e in logs
    ), "BMW is a known import — should not trigger unknown-brand warning"


def test_known_brands_returns_domestic_list_at_least():
    domestic, _import = known_brands()
    assert "Hyundai" in domestic
    assert "BMW" not in domestic  # import


# ── build_q URL integration ───────────────────────────────────────────


def _mk_cfg(brand: str, model_group: str, car_type_code: str = "N") -> ModelConfig:
    return ModelConfig(
        slug=f"{brand}-{model_group}",
        name=f"{brand} {model_group}",
        manufacturer=brand,
        model_group=model_group,
        model=model_group,
        year_from=2020, year_to=2026,
        car_type_code=car_type_code,
    )


def test_build_q_bmw_contains_car_type_n():
    """A BMW query must contain CarType.N — otherwise Encar returns 0."""
    cfg = _mk_cfg("BMW", "X5", car_type_code="N")
    q = build_q(cfg)
    assert "C.CarType.N._." in q, f"BMW query should contain CarType.N: {q}"
    assert "C.Manufacturer.BMW" in q


def test_build_q_genesis_contains_car_type_y():
    """Genesis with Korean manufacturer cell + CarType.Y → correct URL."""
    cfg = _mk_cfg("제네시스", "G80", car_type_code="Y")
    q = build_q(cfg)
    assert "C.CarType.Y._." in q, f"Genesis query should contain CarType.Y: {q}"
    assert "C.Manufacturer.제네시스" in q


def test_build_q_hyundai_english_label_works():
    """English-label Hyundai classifies as domestic via the EN→KR map."""
    cfg = _mk_cfg("Hyundai", "Sonata", car_type_code="Y")
    q = build_q(cfg)
    assert "C.CarType.Y._." in q
    assert "C.Manufacturer.Hyundai" in q  # builder doesn't translate; passes through


def test_build_q_korean_manufacturer_works():
    """Hyundai with the Korean manufacturer string should still classify correctly."""
    cfg = _mk_cfg("현대", "그랜저", car_type_code="Y")
    q = build_q(cfg)
    assert "C.CarType.Y._." in q
    assert "C.Manufacturer.현대" in q


def test_build_q_unknown_brand_defaults_to_n_and_warns():
    """Unknown brand → default N + log a warning, but the URL still assembles."""
    with structlog.testing.capture_logs() as logs:
        cfg = _mk_cfg("MysteryBrand", "Whatever", car_type_code="N")
        q = build_q(cfg)
    assert "C.CarType.N._." in q
    assert any(
        e.get("event") == "cartype_unknown_brand" and e.get("brand") == "MysteryBrand"
        for e in logs
    )


def test_build_q_logs_mismatch_when_explicit_code_disagrees_with_brand():
    """If YAML sets CarType.N for Hyundai (a known domestic), warn."""
    with structlog.testing.capture_logs() as logs:
        cfg = _mk_cfg("Hyundai", "Sonata", car_type_code="N")  # wrong!
        q = build_q(cfg)
    assert "C.CarType.N._." in q  # URL still uses what YAML said
    assert any(
        e.get("event") == "cartype_mismatch_with_brand"
        and e.get("manufacturer") == "Hyundai"
        and e.get("configured") == "N"
        and e.get("derived_from_brand") == "Y"
        for e in logs
    )


def test_build_q_no_warning_when_car_type_matches_brand():
    """BMW with CarType.N → no mismatch warning."""
    with structlog.testing.capture_logs() as logs:
        cfg = _mk_cfg("BMW", "X5", car_type_code="N")
        build_q(cfg)
    assert not any(e.get("event") == "cartype_mismatch_with_brand" for e in logs)


def test_build_q_raw_q_bypasses_classification():
    """When raw_q is set, no CarType cell is built — we just paste verbatim."""
    cfg = ModelConfig(
        slug="manual", name="manual",
        manufacturer="Hyundai",
        model_group="Sonata",
        model="Sonata",
        raw_q="(C.Manufacturer.BMW)",  # whatever — bypasses our logic
        car_type_code="N",
    )
    q = build_q(cfg)
    assert q == "(C.Manufacturer.BMW)"


def test_build_q_no_manufacturer_skips_sanity_check():
    """No manufacturer → no brand-based sanity check, no warning even if
    car_type_code=N looks weird."""
    with structlog.testing.capture_logs() as logs:
        cfg = ModelConfig(
            slug="x", name="x",
            manufacturer=None,
            model_group=None,
            model=None,
            car_type_code="N",
        )
        q = build_q(cfg)
    # Just a CarType cell, no manufacturer inside.
    assert "C.CarType.N._." in q
    assert "C.Manufacturer" not in q
    assert not any(e.get("event") == "cartype_mismatch_with_brand" for e in logs)


# ── build_list_api_url integration ─────────────────────────────────────


def test_build_list_api_url_for_bmw_contains_n_not_y():
    """End-to-end: BMW URL must have CarType.N. Regression for the
    alleged bug where every model got CarType=Y."""
    cfg = _mk_cfg("BMW", "X6", car_type_code="N")
    url = build_list_api_url(cfg)
    assert "C.CarType.N" in url or "CarType%2EN" in url or "CarType.N" in url, (
        f"BMW URL should contain CarType.N, got: {url}"
    )
    assert "C.CarType.Y" not in url, f"BMW URL should NOT contain CarType.Y: {url}"


def test_build_list_api_url_for_genesis_contains_y_not_n():
    cfg = _mk_cfg("제네시스", "G80", car_type_code="Y")
    url = build_list_api_url(cfg)
    assert "CarType.Y" in url or "CarType%2EY" in url, f"Genesis URL should contain CarType.Y: {url}"
    assert "CarType.N" not in url, f"Genesis URL should NOT contain CarType.N: {url}"


# ── ModelConfig default ────────────────────────────────────────────────


def test_model_config_default_car_type_code_is_n():
    """Default must be 'N' (import) — safer for unknown brands than 'Y'."""
    cfg = ModelConfig(slug="x", name="x")
    assert cfg.car_type_code == CAR_TYPE_IMPORT


# ── DOMESTIC_BRANDS_EN_TO_KR coverage ─────────────────────────────────


def test_all_documented_domestic_brands_have_korean_transliteration():
    """Every English label in the dict must map to a non-empty Korean string."""
    for en, kr in DOMESTIC_BRANDS_EN_TO_KR.items():
        assert en and kr, f"empty entry in DOMESTIC_BRANDS_EN_TO_KR: {en!r} -> {kr!r}"
        # Sanity: Korean text should contain Hangul characters (U+AC00–U+D7A3).
        assert any("가" <= c <= "힣" for c in kr), (
            f"{kr!r} doesn't look like Korean text"
        )
