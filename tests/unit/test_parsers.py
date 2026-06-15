import pytest
import json
from datetime import date

from encar_parser.parsers.list_page import parse_search_list, SearchListItem
from encar_parser.parsers.details import parse_car_detail


# ---- list parser ----

def test_parse_search_list_extracts_ids():
    payload = {
        "SearchResults": {
            "EncarSearchResults": [
                {"Id": 42131435, "Manufacturer": "BMW", "Model": "X5 (G05)"},
                {"Id": 42131436, "Manufacturer": "BMW", "Model": "X5 (G05)"},
            ]
        }
    }
    items = parse_search_list(payload)
    assert len(items) == 2
    assert items[0].encar_id == 42131435
    assert items[0].brand == "BMW"
    assert items[0].model == "X5 (G05)"


def test_parse_search_list_handles_empty():
    payload = {"SearchResults": {"EncarSearchResults": []}}
    assert parse_search_list(payload) == []


def test_parse_search_list_handles_missing_key():
    """If structure differs, return empty list rather than crash."""
    assert parse_search_list({}) == []


# ---- details parser ----

def test_parse_car_detail_full():
    payload = {
        "car": {
            "vehicleNo": "158바6820",
            "year": "2025-11",
            "mileage": "4,027",
            "displacement": "2998",
            "fuel": {"name": "가솔린"},
            "transmission": {"name": "오토"},
            "bodyType": "SUV",
            "color": {"name": "검정색"},
            "seats": "5",
            "importType": {"name": "정식수입"},
            "manufacturer": "BMW",
            "manufacturerWarranty": "BMW",
            "liens": "0건",
            "seizures": "0건",
            "accidentRecords": 376,
            "price": "128500000",
            "photos": [
                "https://img.encar.com/car1/42131435_001.jpg",
                "https://img.encar.com/car1/42131435_002.jpg",
            ],
        }
    }
    car = parse_car_detail(encar_id=42131435, payload=payload)
    assert car.encar_id == 42131435
    assert car.brand == "BMW"  # passed in
    assert car.year_month == date(2025, 11, 1)
    assert car.mileage_km == 4027
    assert car.displacement_cc == 2998
    assert car.fuel_ru == "Бензин"
    assert car.fuel_original == "가솔린"
    assert car.transmission_ru == "Автомат"
    assert car.body_type == "SUV"
    assert car.color_ru == "Чёрный"
    assert car.seats == 5
    assert car.import_type_ru == "Официальный"
    assert car.liens_seizures == "0건·0건"
    assert car.accident_records == 376
    assert car.price_krw == 128500000
    assert len(car.photo_urls) == 2
    assert car.encar_detail_url == "https://fem.encar.com/cars/detail/42131435"


def test_parse_car_detail_handles_missing_optional_fields():
    payload = {"car": {"year": "2020-01"}}
    car = parse_car_detail(encar_id=1, payload=payload, brand="Kia", model="Rio")
    assert car.encar_id == 1
    assert car.brand == "Kia"
    assert car.year_month == date(2020, 1, 1)
    assert car.mileage_km is None
    assert car.fuel_ru is None