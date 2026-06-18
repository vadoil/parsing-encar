from datetime import date
from pathlib import Path

import pytest

from encar_parser.parsers.details import parse_car_detail
from encar_parser.parsers.list_page import parse_search_list


# --- Phase 1: real-fixture tests (ground truth = output/_details.json) -----


REAL_DETAILS_PATH = Path(__file__).resolve().parents[2] / "output" / "_details.json"


@pytest.fixture(scope="module")
def real_details() -> dict[str, dict]:
    """Load 30 real BMW X5 G05 detail responses, keyed by JSON-id (str)."""
    if not REAL_DETAILS_PATH.exists():
        pytest.skip(f"ground-truth fixture missing: {REAL_DETAILS_PATH}")
    import json
    with REAL_DETAILS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_real_recordview_true_yields_report_available_true(real_details: dict[str, dict]) -> None:
    """29/30 cars in the sample have condition.accident.recordView=True.

    The new field accident_report_available must be True for all of them —
    renamed honestly from the old int 'accident_records: 1' that misled users
    into thinking 96.7% of cars had a real accident.
    """
    seen_true = 0
    for json_id, payload in real_details.items():
        car = parse_car_detail(encar_id=int(payload["vehicleId"]), payload=payload)
        rv = (payload.get("condition", {}).get("accident", {}) or {}).get("recordView")
        if rv is True:
            assert car.accident_report_available is True, (
                f"car {json_id}: recordView=True but report_available != True"
            )
            seen_true += 1
    assert seen_true >= 25, f"expected most cars to have recordView=True, got {seen_true}"


def test_real_recordview_false_yields_report_available_false(real_details: dict[str, dict]) -> None:
    """The 1 car with recordView=False (ID 40690603 per field-map.md) must
    parse to accident_report_available=False."""
    found = False
    for json_id, payload in real_details.items():
        rv = (payload.get("condition", {}).get("accident", {}) or {}).get("recordView")
        if rv is False:
            car = parse_car_detail(encar_id=int(payload["vehicleId"]), payload=payload)
            assert car.accident_report_available is False, (
                f"car {json_id}: recordView=False but report_available != False"
            )
            found = True
    assert found, "fixture must contain at least one recordView=False car"


def test_real_condition_insurance_is_null_in_all_samples(real_details: dict[str, dict]) -> None:
    """Phase 0 finding: condition.insurance is null for every car in the
    sample. Document this assumption as a regression guard so future
    Encar API changes are noticed.
    """
    insurance_values = {
        json_id: (payload.get("condition", {}) or {}).get("insurance")
        for json_id, payload in real_details.items()
    }
    assert all(v is None for v in insurance_values.values()), (
        f"expected all condition.insurance=None, found non-null: "
        f"{[k for k, v in insurance_values.items() if v is not None]}"
    )

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


def test_parse_search_list_real_api_shape():
    """Real api.encar.com shape: SearchResults is a top-level list."""
    payload = {
        "Count": 2,
        "SearchResults": [
            {"Id": "42131435", "Manufacturer": "BMW", "Model": "X5"},
            {"Id": "42131436", "Manufacturer": "BMW", "Model": "X5"},
        ],
    }
    items = parse_search_list(payload)
    assert [i.encar_id for i in items] == [42131435, 42131436]


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
    assert car.accident_report_available is True  # legacy int 376 → bool True
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


def test_parse_car_detail_real_api_shape():
    """Real api.encar.com/v1/readside/vehicle/{id} flat shape (no `car` wrapper)."""
    payload = {
        "manage": {"viewCount": 464, "subscribeCount": 5},
        "category": {
            "manufacturerName": "BMW",
            "modelName": "X5 (G05)",
            "yearMonth": "202511",
            "importType": "REGULAR_IMPORT",
            "warranty": {"companyName": "BMW"},
        },
        "advertisement": {"price": 13300, "status": "ADVERTISE"},
        "contact": {"userId": "sorkdlrla", "address": "경기 용인시 기흥구 중부대로 242"},
        "spec": {
            "mileage": 4027,
            "displacement": 2998,
            "transmissionName": "오토",
            "fuelName": "가솔린",
            "colorName": "검정색",
            "seatCount": 5,
            "bodyName": "SUV",
        },
        "photos": [
            {"path": "/carpicture03/pic4213/42131435_001.jpg", "type": "OUTER"},
            {"path": "/carpicture03/pic4213/42131435_002.jpg", "type": "OUTER"},
        ],
        "options": {"standard": ["001", "002"]},
        "condition": {
            "accident": {"recordView": True, "resumeView": True},
            "seizing": {"seizingCount": 0, "pledgeCount": 0},
        },
        "partnership": {"dealer": {"name": "송범기"}},
        "view": {"encarDiagnosis": 1},
        "vehicleId": 42131435,
        "vehicleType": "CAR",
        "vin": "WBA21EU01T9150211",
        "vehicleNo": "158버6820",
    }
    car = parse_car_detail(encar_id=42131435, payload=payload)
    assert car.encar_id == 42131435
    assert car.brand == "BMW"
    assert car.model == "X5 (G05)"
    assert car.year_month == date(2025, 11, 1)
    assert car.mileage_km == 4027
    assert car.displacement_cc == 2998
    assert car.fuel_original == "가솔린"
    assert car.fuel_ru == "Бензин"
    assert car.transmission_orig == "오토"
    assert car.transmission_ru == "Автомат"
    assert car.body_type == "SUV"
    assert car.color_original == "검정색"
    assert car.color_ru == "Чёрный"
    assert car.seats == 5
    assert car.import_type_ru == "Официальный"
    assert car.plate_number == "158버6820"
    assert car.accident_report_available is True  # recordView=True → report available
    assert car.liens_seizures == "0건·0건"
    assert car.price_krw == 133000000  # 13300 만원
    assert car.manufacturer_warranty == "BMW"
    assert len(car.photo_urls) == 2
    assert car.photo_urls[0].endswith("42131435_001.jpg")
    assert car.encar_detail_url == "https://fem.encar.com/cars/detail/42131435"
