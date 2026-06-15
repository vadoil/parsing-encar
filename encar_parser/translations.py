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
    "검정색": "Чёрный",
    "흰색": "Белый",
    "회색": "Серый",
    "은색": "Серебристый",
    "파란색": "Синий",
    "빨간색": "Красный",
    "노란색": "Жёлтый",
    "녹색": "Зелёный",
    "갈색": "Коричневый",
    "보라색": "Фиолетовый",
}

IMPORT_TYPE_KO_TO_RU: dict[str, str] = {
    "정식수입": "Официальный",
    "병행수입": "Параллельный",
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
