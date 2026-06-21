import pytest
import structlog.testing

from encar_parser.translations import (
    reset_untranslated_color_cache,
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
        ("쥐색", "Серый"),
        ("은색", "Серебристый"),
        ("은하색", "Серебристый"),       # NEW: galaxy
        ("은회색", "Серебристый"),
        ("파란색", "Синий"),
        ("청색", "Синий"),
        ("군청색", "Тёмно-синий"),      # NEW
        ("하늘색", "Голубой"),          # NEW
        ("빨간색", "Красный"),
        ("베이지색", "Бежевый"),
        ("샴페인", "Шампань"),
        ("진주색", "Перламутровый"),   # NEW
        ("BLACK", "Чёрный"),
        # Hanja / alias variants (added 2026-06-21)
        ("백색", "Белый"),
        ("흑색", "Чёрный"),
        ("적색", "Красный"),
        ("초록색", "Зелёный"),
    ],
)
def test_translate_color(korean, russian):
    reset_untranslated_color_cache()
    assert translate_color(korean) == russian


def test_translate_color_unknown_returns_original():
    """Unknown color passes through unchanged so the UI can still display it."""
    reset_untranslated_color_cache()
    assert translate_color("연보라색") == "연보라색"


def test_translate_color_unknown_logs_warning_once():
    """A new unknown color must log a warning so we can extend the map.
    Subsequent calls must NOT re-warn (per-row spam would flood encar.log)."""
    reset_untranslated_color_cache()
    with structlog.testing.capture_logs() as logs:
        translate_color("신비한색")
        translate_color("신비한색")
        translate_color("신비한색")
    warn_events = [e for e in logs if e.get("event") == "untranslated_color"]
    assert len(warn_events) == 1, f"expected exactly one warning, got {len(warn_events)}"
    assert warn_events[0]["color"] == "신비한색"


def test_translate_color_empty_value_returns_empty():
    """None/empty passes through without warning."""
    reset_untranslated_color_cache()
    assert translate_color("") == ""
    assert translate_color(None) is None


def test_reset_untranslated_color_cache_clears_warned_set():
    """Tests that need a fresh warning use the helper to reset state."""
    reset_untranslated_color_cache()
    with structlog.testing.capture_logs() as logs1:
        translate_color("임시색")
    assert any(e.get("event") == "untranslated_color" for e in logs1)
    # Without reset, second call does NOT warn.
    with structlog.testing.capture_logs() as logs2:
        translate_color("임시색")
    assert not any(e.get("event") == "untranslated_color" for e in logs2)
    # After reset, warning fires again.
    reset_untranslated_color_cache()
    with structlog.testing.capture_logs() as logs3:
        translate_color("임시색")
    assert any(e.get("event") == "untranslated_color" for e in logs3)


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
