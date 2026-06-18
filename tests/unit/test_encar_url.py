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
    assert q.startswith("(And.(")
    assert "C.CarType." in q
    assert "C.Manufacturer.BMW" in q
    assert "C.ModelGroup.X5" in q


def test_build_q_with_model_nests_deepest():
    cfg = ModelConfig(
        slug="x", name="x", manufacturer="BMW", model_group="X5", model="X5 (G05)"
    )
    q = build_q(cfg)
    assert "Model.X5 (G05)" in q
    # deepest level uses bare field name, not C.Model
    assert "C.Model." not in q


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
