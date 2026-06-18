from urllib.parse import unquote

from encar_parser.encar_url import (
    ModelConfig,
    build_action,
    build_list_api_url,
    build_q,
    build_sr,
    build_url,
)


def test_build_q_minimal():
    cfg = ModelConfig(
        slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5"
    )
    q = build_q(cfg)
    assert q.startswith("(And.Hidden.N._.")
    assert "C.CarType." in q
    assert "C.Manufacturer.BMW" in q
    # ModelGroup is the deepest cell in this case → bare, no C. prefix.
    assert "ModelGroup.X5" in q
    assert "C.ModelGroup" not in q


def test_build_q_with_model_in_config_does_not_change_q():
    # The 'model' field on config is metadata only — encar's search expression
    # stops at ModelGroup level (per the raw_q reference captured from DevTools).
    # The model name is stored on the Car record but does not appear in `q`.
    cfg = ModelConfig(
        slug="x", name="x", manufacturer="BMW", model_group="X5", model="X5 (G05)"
    )
    q = build_q(cfg)
    assert "Model.X5 (G05)" not in q
    assert "C.Model." not in q
    assert "ModelGroup.X5" in q


def test_build_q_with_year_range():
    cfg = ModelConfig(
        slug="bmw-x5-g05", name="BMW X5 (G05)",
        manufacturer="BMW", model_group="X5",
        year_from=2018, year_to=2026,
    )
    q = build_q(cfg)
    assert "Year.range(201800..202699)" in q


def test_build_q_no_year_range_when_only_one_bound():
    cfg = ModelConfig(
        slug="x", name="x", manufacturer="BMW", model_group="X5",
        year_from=2018, year_to=None,
    )
    q = build_q(cfg)
    assert "Year.range" not in q


def test_build_q_golden_bmw_matches_raw_q():
    """Golden test: build_q(bmw_x5_g05_config) MUST equal the captured raw_q.

    The raw_q in models.yaml was copied verbatim from DevTools (Network tab on
    a real search of BMW X5 G05). It is the source of truth for the q format.
    """
    raw_q = "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.ModelGroup.X5.))_.Year.range(201800..202699).)"  # noqa: E501
    cfg = ModelConfig(
        slug="bmw-x5-g05", name="BMW X5 (G05)",
        manufacturer="BMW", model_group="X5", model="X5 (G05)",
        year_from=2018, year_to=2026,
        sort="ModifiedDate",
        car_type_code="N",
    )
    assert build_q(cfg) == raw_q


def test_build_q_raw_override():
    raw = "(And.(C.CarType.Y._.C.Manufacturer.벤츠.))"
    cfg = ModelConfig(slug="x", name="x", manufacturer="BMW", raw_q=raw)
    assert build_q(cfg) == raw


def test_build_sr_pagination():
    cfg = ModelConfig(slug="x", name="x", limit=20)
    assert build_sr(cfg, page=1) == "|ModifiedDate|0|20"
    assert build_sr(cfg, page=3) == "|ModifiedDate|40|20"


def test_build_list_api_url():
    cfg = ModelConfig(
        slug="bmw-x5-g05", name="BMW X5 (G05)", manufacturer="BMW", model_group="X5"
    )
    url = build_url(cfg)
    assert url.startswith("https://api.encar.com/search/car/list/general?")
    decoded = unquote(url)
    assert "q=(And." in decoded
    assert "Manufacturer.BMW" in decoded
    assert "sr=|ModifiedDate|0|20" in decoded
    # build_url and build_list_api_url agree
    assert url == build_list_api_url(cfg)


def test_build_action_reference_payload():
    cfg = ModelConfig(slug="x", name="x", manufacturer="BMW", model_group="X5")
    action = build_action(cfg)
    assert "q" in action and "sr" in action
    assert action["api_url"].startswith("https://api.encar.com/")
    assert action["frontend_url"].startswith("https://www.encar.com/")
