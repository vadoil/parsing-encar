"""Unit tests for brand_display — Korean→English reverse map for UI."""
from __future__ import annotations

import pytest

from encar_parser.brand_display import brand_display


@pytest.mark.parametrize(
    ("korean", "english"),
    [
        ("현대", "Hyundai"),
        ("기아", "Kia"),
        ("제네시스", "Genesis"),
        ("쌍용", "Ssangyong"),
        ("KG모빌리티", "KG Mobility"),
        ("르노코리아", "Renault Korea"),
        ("한국GM", "GM Korea"),
        ("쉐보레", "Chevrolet"),
        ("대우", "Daewoo"),
        ("삼성", "Samsung"),
    ],
)
def test_brand_display_korean_to_english(korean, english):
    """Korean transliterations map to the English catalog label."""
    assert brand_display(korean) == english


@pytest.mark.parametrize(
    "english",
    [
        "Hyundai", "Kia", "Genesis", "Ssangyong", "BMW",
        "Mercedes-Benz", "Audi", "Toyota", "Lexus", "Volkswagen",
        "Land Rover", "Jaguar", "Porsche", "Ferrari",
    ],
)
def test_brand_display_english_passes_through(english):
    """English labels (imports + already-English domestic names) are returned as-is."""
    assert brand_display(english) == english


def test_brand_display_unknown_passes_through():
    """Unknown brand — same policy as car_type.classify_brand: return as-is
    rather than silent-fallback to something generic."""
    assert brand_display("ЗАЗ") == "ЗАЗ"
    assert brand_display("MysteryBrand") == "MysteryBrand"


def test_brand_display_empty_returns_empty():
    assert brand_display("") == ""
    assert brand_display(None) == ""


def test_brand_display_does_not_translate_imports_to_english():
    """Defensive check: even if an import brand happens to share a Korean
    transliteration with a domestic one, we never touch imports."""
    # Hypothetical: if "BMW" ever appeared as a Korean string in the DB
    # (it doesn't, but…), brand_display("BMW") returns "BMW" — not "Hyundai".
    assert brand_display("BMW") == "BMW"
