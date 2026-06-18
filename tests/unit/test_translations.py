import pytest

from encar_parser.translations import (
    translate_color,
    translate_fuel,
    translate_import_type,
    translate_transmission,
)


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("가솔린", "Бензин"),
        ("디젤", "Дизель"),
        ("하이브리드", "Гибрид"),
        ("전기", "Электро"),
        ("LPG", "Газ"),
        ("가스", "Газ"),
    ],
)
def test_translate_fuel(korean, russian):
    assert translate_fuel(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("오토", "Автомат"),
        ("수동", "Механика"),
        ("CVT", "Вариатор"),
        ("DCT", "Робот"),
        ("자동", "Автомат"),
    ],
)
def test_translate_transmission(korean, russian):
    assert translate_transmission(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("검정색", "Чёрный"),
        ("흰색", "Белый"),
        ("회색", "Серый"),
        ("은색", "Серебристый"),
        ("파란색", "Синий"),
        ("빨간색", "Красный"),
    ],
)
def test_translate_color(korean, russian):
    assert translate_color(korean) == russian


@pytest.mark.parametrize(
    ("korean", "russian"),
    [
        ("정식수입", "Официальный"),
        ("병행수입", "Параллельный"),
    ],
)
def test_translate_import_type(korean, russian):
    assert translate_import_type(korean) == russian


def test_translate_unknown_fuel_returns_original():
    """Unknown values pass through unchanged for visibility."""
    assert translate_fuel("수소") == "수소"
