"""Korean → Russian translation dictionaries for encar fields."""

from encar_parser.utils.log import get_logger

log = get_logger(__name__)

FUEL_KO_TO_RU: dict[str, str] = {
    "가솔린": "Бензин",
    "디젤": "Дизель",
    "하이브리드": "Гибрид",
    "전기": "Электро",
    "LPG": "Газ",
    "가스": "Газ",
}

TRANSMISSION_KO_TO_RU: dict[str, str] = {
    "오토": "Автомат",
    "자동": "Автомат",
    "수동": "Механика",
    "CVT": "Вариатор",
    "DCT": "Робот",
    "로봇": "Робот",
}

# Set of Korean color values we've already seen and warned about, so we
# don't spam encar.log once per row.
_untranslated_colors_warned: set[str] = set()


COLOR_KO_TO_RU: dict[str, str] = {
    # Primary colors (also exposed by the v1/readside/vehicle/{id} API).
    "검정색": "Чёрный",
    "흑색": "Чёрный",          # Hanja (hanja) variant of 검정색
    "흰색": "Белый",
    "백색": "Белый",           # Hanja variant — встречается у Genesis и пр.
    "회색": "Серый",
    "쥐색": "Серый",          # "mouse-grey" — Korean variant of Серый
    "은색": "Серебристый",
    "은회색": "Серебристый",    # "silver-grey" — встречается у премиум-комплектаций
    "은하색": "Серебристый",    # "galaxy" — спецкомплектации Hyundai/Kia
    "파란색": "Синий",
    "청색": "Синий",            # "navy/cyan blue" — встречается у новых BMW/Mercedes
    "군청색": "Тёмно-синий",    # "prussian/navy blue"
    "하늘색": "Голубой",        # "sky blue"
    "빨간색": "Красный",
    "적색": "Красный",          # Hanja variant
    "노란색": "Жёлтый",
    "녹색": "Зелёный",
    "초록색": "Зелёный",        # alias для зелёного
    "갈색": "Коричневый",
    "보라색": "Фиолетовый",
    # Beige / champagne — встречаются у премиум-комплектаций (BMW X5 и т.п.).
    "베이지": "Бежевый",
    "베이지색": "Бежевый",
    "샴페인": "Шампань",
    "골드": "Золотой",
    "금색": "Золотой",
    "주황색": "Оранжевый",
    # Pearl — встречается у Lexus ES, Genesis G80/G90 и пр.
    "진주색": "Перламутровый",
    # English codes the API sometimes returns.
    "BLACK": "Чёрный",
    "WHITE": "Белый",
    "GRAY": "Серый",
    "GREY": "Серый",
    "SILVER": "Серебристый",
    "BLUE": "Синий",
    "RED": "Красный",
    "YELLOW": "Жёлтый",
    "GREEN": "Зелёный",
    "BROWN": "Коричневый",
    "BEIGE": "Бежевый",
    "GOLD": "Золотой",
    "ORANGE": "Оранжевый",
}

IMPORT_TYPE_KO_TO_RU: dict[str, str] = {
    "정식수입": "Официальный",
    "병행수입": "Параллельный",
    # English codes used by the v1/readside/vehicle/{id} endpoint.
    "REGULAR_IMPORT": "Официальный",
    "PARALLEL_IMPORT": "Параллельный",
}


def translate_fuel(value: str) -> str:
    """Translate fuel type from Korean to Russian. Unknown values pass through."""
    return FUEL_KO_TO_RU.get(value, value)


def translate_transmission(value: str) -> str:
    """Translate transmission type from Korean to Russian. Unknown values pass through."""
    return TRANSMISSION_KO_TO_RU.get(value, value)


def translate_color(value: str) -> str:
    """Translate color from Korean to Russian.

    Unknown values pass through unchanged AND log a one-shot
    ``untranslated_color`` warning so we can extend the map when a
    new color shows up in the catalog.
    """
    if not value:
        return value
    if value in COLOR_KO_TO_RU:
        return COLOR_KO_TO_RU[value]
    if value not in _untranslated_colors_warned:
        log.warning(
            "untranslated_color",
            color=value,
            hint="add to COLOR_KO_TO_RU in encar_parser.translations",
        )
        _untranslated_colors_warned.add(value)
    return value


def translate_import_type(value: str) -> str:
    """Translate import type from Korean to Russian. Unknown values pass through."""
    return IMPORT_TYPE_KO_TO_RU.get(value, value)


def reset_untranslated_color_cache() -> None:
    """Test helper: forget already-warned colors so a fresh run warns again."""
    _untranslated_colors_warned.clear()
