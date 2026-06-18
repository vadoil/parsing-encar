"""Unit tests for pipeline.make_list_url_for_page.

The function must produce a valid paginated URL from the stored encar_action
JSON. Regression: parse_qs+urlencode produced `key=['v']` (list literal in
the URL) instead of `key=v`. parse_qsl avoids this.
"""
from urllib.parse import unquote

from encar_parser.pipeline import make_list_url_for_page


def _bmw_action() -> dict:
    return {
        "q": "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.ModelGroup.X5.))_.Year.range(201800..202699).)",
        "sr": "|ModifiedDate|0|20",
        "api_url": "https://api.encar.com/search/car/list/general?count=true&q=(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.ModelGroup.X5.))_.Year.range(201800..202699).)&sr=%7CModifiedDate%7C0%7C20",
        "sort": "ModifiedDate",
        "limit": 20,
    }


def test_page_1_uses_zero_offset():
    url = make_list_url_for_page(_bmw_action(), 1)
    decoded = unquote(url)
    assert "sr=|ModifiedDate|0|20" in decoded
    # Must NOT have list-literal artifacts from the parse_qs bug.
    assert "[" not in url
    assert "]" not in url
    assert "'" not in url


def test_page_2_uses_offset_20():
    url = make_list_url_for_page(_bmw_action(), 2)
    decoded = unquote(url)
    assert "sr=|ModifiedDate|20|20" in decoded
    assert "[20" not in url


def test_page_3_uses_offset_40():
    url = make_list_url_for_page(_bmw_action(), 3)
    decoded = unquote(url)
    assert "sr=|ModifiedDate|40|20" in decoded


def test_keeps_count_and_q_params():
    url = make_list_url_for_page(_bmw_action(), 2)
    assert "count=true" in url
    assert "ModelGroup.X5" in url
    # sr is present exactly once (no duplicate keys).
    assert url.count("sr=") == 1


def test_clamps_page_below_1():
    url = make_list_url_for_page(_bmw_action(), 0)
    assert "sr=|ModifiedDate|0|20" in unquote(url)
    assert "sr=|ModifiedDate|-20|20" not in unquote(url)
