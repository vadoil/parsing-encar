from encar_parser.utils.ua import USER_AGENTS


def test_ua_pool_has_at_least_10_entries():
    assert len(USER_AGENTS) >= 10


def test_ua_strings_look_realistic():
    for ua in USER_AGENTS:
        assert ua.startswith("Mozilla/5.0")
        assert any(b in ua for b in ("Chrome", "Safari", "Firefox", "Edg"))
