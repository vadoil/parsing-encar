"""Unit tests for the front-end Encar URL builder.

The builder pulls the S-expression from ``SearchModel.encar_action["q"]``
(the same string we use for the API call) and wraps it in the
``carType=`` + JSON-hash URL format Encar's front-end expects.
"""
from __future__ import annotations

import json
import urllib.parse

from encar_parser.db.models import SearchModel
from encar_parser.web.links import encar_web_url


def _mk(slug: str, *, q: str, car_type_code: str = "N") -> SearchModel:
    return SearchModel(
        slug=slug, name=slug, encar_url="",
        encar_action={"q": q, "sr": "|ModifiedDate|0|20",
                      "api_url": "https://api.encar.com/...",
                      "sort": "ModifiedDate", "limit": 20},
        enabled=True, priority=100,
    )


def test_encar_web_url_bmw_uses_for_and_correct_action():
    sm = _mk("bmw-x6-g06", q=(
        "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._."
        "(C.ModelGroup.X6._.Model.X6 (G06).)))_.Year.range(201800..202699).)"
    ))
    url = encar_web_url(sm)
    assert url.startswith("https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!")
    payload = json.loads(urllib.parse.unquote(url.split("#!", 1)[1]))
    assert payload["action"].startswith("(And.Hidden.N._.(C.CarType.N._.")
    assert "C.Manufacturer.BMW" in payload["action"]
    assert "C.ModelGroup.X6" in payload["action"]
    assert payload["sort"] == "ModifiedDate"
    assert payload["page"] == 1
    assert payload["limit"] == 20
    assert payload["loginCheck"] is False


def test_encar_web_url_genesis_uses_kor():
    """A domestic model must use carType=kor — the existing
    encar_action['frontend_url'] always says 'for' (it predates the
    hash payload and never used the lookup). This is the bug the
    helper fixes."""
    sm = _mk("genesis-g80", car_type_code="Y", q=(
        "(And.Hidden.N._.(C.CarType.Y._.(C.Manufacturer.제네시스._."
        "(C.ModelGroup.G80._.Model.G80 (RG3).)))_.Year.range(201800..202699).)"
    ))
    url = encar_web_url(sm)
    assert "carType=kor" in url
    payload = json.loads(urllib.parse.unquote(url.split("#!", 1)[1]))
    assert "C.CarType.Y" in payload["action"]
    assert "C.Manufacturer.제네시스" in payload["action"]


def test_encar_web_url_unicode_safe():
    """Russian/Korean characters must survive URL encoding."""
    sm = _mk("kia-sorento", car_type_code="Y", q=(
        "(And.Hidden.N._.(C.CarType.Y._.(C.Manufacturer.기아._."
        "(C.ModelGroup.쏘렌토._.Model.쏘렌토 (MQ4).)))_.Year.range(201800..202699).)"
    ))
    url = encar_web_url(sm)
    payload = json.loads(urllib.parse.unquote(url.split("#!", 1)[1]))
    assert "기아" in payload["action"]
    assert "쏘렌토" in payload["action"]


def test_encar_web_url_car_type_param_driven_by_action_not_by_row_field():
    """The carType= param is derived from the action S-expression, not from
    sm.car_type_code. A row with car_type_code=N but action containing
    CarType.Y must still get carType=kor — the action is authoritative."""
    sm = _mk("weird", car_type_code="N", q=(
        "(C.CarType.Y._.(C.Manufacturer.Hyundai._.))"
    ))
    url = encar_web_url(sm)
    assert "carType=kor" in url


def test_encar_web_url_uses_action_verbatim():
    """Whatever q is in the DB is what gets embedded — no reconstruction."""
    raw = (
        "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.Porsche._."
        "(C.ModelGroup.911._.Model.911.)))_.Year.range(201800..202699).)"
    )
    sm = _mk("porsche-911", q=raw)
    payload = json.loads(urllib.parse.unquote(encar_web_url(sm).split("#!", 1)[1]))
    assert payload["action"] == raw


def test_encar_web_url_missing_q_uses_degenerate_cell():
    """Old DB rows might not have q at all. The link still works —
    we fall back to a (CarType.{N}._.(Manufacturer.X._.)) cell."""
    sm = SearchModel(
        slug="orphan", name="orphan", encar_url="",
        encar_action={"sort": "ModifiedDate", "limit": 20},
        enabled=True, priority=100,
    )
    url = encar_web_url(sm)
    # Should still be a valid URL even with empty q.
    assert url.startswith("https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!")


def test_encar_web_url_payload_keys_are_stable():
    """The JSON hash always has the same set of keys (Encar parses by name)."""
    sm = _mk("bmw-x5-g05", q=(
        "(C.CarType.N._.(C.Manufacturer.BMW._.ModelGroup.X5.))"
    ))
    payload = json.loads(urllib.parse.unquote(encar_web_url(sm).split("#!", 1)[1]))
    assert set(payload.keys()) == {
        "action", "toggle", "layer", "sort", "page",
        "limit", "searchKey", "loginCheck",
    }


def test_encar_web_url_returns_str_type():
    """The helper must return ``str`` so Jinja2 can render it inline."""
    sm = _mk("bmw-x5-g05", q="(C.CarType.N._.)")
    assert isinstance(encar_web_url(sm), str)
