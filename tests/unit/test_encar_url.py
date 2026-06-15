import pytest

from encar_parser.encar_url import ModelConfig, build_action, build_url


def test_build_action_minimal():
    cfg = ModelConfig(slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5")
    action = build_action(cfg)

    assert "Manufacturer.BMW" in action["action"]
    assert "ModelGroup.X5" in action["action"]
    assert "Hidden.N" in action["action"]
    assert "CarType.N" in action["action"]
    assert action["sort"] == "ModifiedDate"
    assert action["limit"] == 20
    assert action["page"] == 1


def test_build_action_with_year_range():
    cfg = ModelConfig(
        slug="x", name="x",
        manufacturer="BMW", model="X5 (G05)",
        year_from=2018, year_to=2025,
    )
    action = build_action(cfg)
    payload = action["action"]
    assert "Model.X5" in payload
    # Year range encoded in action
    assert "2018" in payload or "Year" in payload


def test_build_action_with_optional_filters():
    cfg = ModelConfig(
        slug="x", name="x",
        manufacturer="Kia", model_group="Sportage", model="Sportage",
        fuel="hybrid", transmission="automatic", body_type="SUV",
    )
    action = build_action(cfg)
    assert "Fuel.hybrid" in action["action"]
    assert "Transmission.automatic" in action["action"]
    assert "BodyType.SUV" in action["action"]


def test_build_url_returns_full_url():
    cfg = ModelConfig(slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5")
    url = build_url(cfg)
    assert url.startswith("https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!")
    assert "Manufacturer.BMW" in url
