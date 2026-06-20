"""Korean → Russian translation dictionaries for encar fields."""

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

COLOR_KO_TO_RU: dict[str, str] = {
    # Primary colors (also exposed by the v1/readside/vehicle/{id} API).
    "검정색": "Чёрный",
    "흰색": "Белый",
    "회색": "Серый",
    "쥐색": "Серый",          # "mouse-grey" — Korean variant of Серый
    "은색": "Серебристый",
    "은회색": "Серебристый",    # "silver-grey" — встречается у премиум-комплектаций
    "파란색": "Синий",
    "청색": "Синий",            # "navy/cyan blue" — встречается у новых BMW/Mercedes
    "빨간색": "Красный",
    "노란색": "Жёлтый",
    "녹색": "Зелёный",
    "갈색": "Коричневый",
    "보라색": "Фиолетовый",
    # Beige / champagne — встречаются у премиум-комплектаций (BMW X5 и т.п.).
    "베이지": "Бежевый",
    "베이지색": "Бежевый",
    "샴페인": "Шампань",
    "골드": "Золотой",
    "금색": "Золотой",
    "주황색": "Оранжевый",
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
    """Translate color from Korean to Russian. Unknown values pass through."""
    return COLOR_KO_TO_RU.get(value, value)


def translate_import_type(value: str) -> str:
    """Translate import type from Korean to Russian. Unknown values pass through."""
    return IMPORT_TYPE_KO_TO_RU.get(value, value)
