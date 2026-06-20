"""Unit tests for the photo-URL extractor used by the web viewer.

The extractor has to handle photo_urls that arrived in the DB in different
shapes over time — old runs stored plain strings (sometimes with full
URL, sometimes as a relative path), newer runs should also be strings,
but we keep the door open for objects with `path` / `url` keys in case
encar ever returns that shape. Whichever the form, the function must:

* return the first usable URL
* normalize to a full https URL on the working photo CDN (ci.encar.com)
* return None for any empty / null / malformed input (the caller renders
  a "no photo" placeholder rather than crashing the page)
"""
from __future__ import annotations

import pytest

from encar_parser.photos import first_photo_url, first_photo_proxy_src


# ── first_photo_url: raw URL extraction + normalization ────────────────


def test_empty_inputs_yield_none():
    assert first_photo_url(None) is None
    assert first_photo_url([]) is None
    assert first_photo_url("not a list") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "photo",
    [
        "https://ci.encar.com/carpicture06/x/1_001.jpg",
        "https://img.encar.com/carpicture06/x/1_001.jpg",  # legacy host
        "/carpicture06/x/1_001.jpg",                       # bare path (oldest format)
    ],
)
def test_first_string_url_is_returned_as_absolute(photo):
    """All three legacy forms should normalize to a full https URL on
    ci.encar.com — the working photo CDN."""
    out = first_photo_url([photo])
    assert out == "https://ci.encar.com/carpicture06/x/1_001.jpg"


def test_picks_index_zero_only():
    """The spec says: 'миниатюру ПЕРВОЙ (индекс 0)'."""
    urls = [
        "https://ci.encar.com/x/1.jpg",
        "https://ci.encar.com/x/2.jpg",
        "https://ci.encar.com/x/3.jpg",
    ]
    assert first_photo_url(urls) == "https://ci.encar.com/x/1.jpg"


def test_handles_dict_with_path_key():
    """Forward-compat: if photo_urls ever switches to objects, keep working."""
    out = first_photo_url([{"path": "/carpicture06/x/2_001.jpg", "code": "001"}])
    assert out == "https://ci.encar.com/carpicture06/x/2_001.jpg"


def test_handles_dict_with_url_key():
    out = first_photo_url([{"url": "https://ci.encar.com/x/3.jpg"}])
    assert out == "https://ci.encar.com/x/3.jpg"


def test_skips_empty_or_invalid_entries_and_uses_next():
    """If the first entry is empty/garbage but the second is real, use the
    second rather than rendering a broken image. Common when an old row
    has an empty-string first element."""
    out = first_photo_url([
        "", None, "https://ci.encar.com/x/real.jpg"
    ])
    assert out == "https://ci.encar.com/x/real.jpg"


def test_returns_none_when_all_entries_invalid():
    assert first_photo_url([None, "", {}]) is None
    assert first_photo_url([{"foo": "bar"}]) is None  # dict with no path/url


def test_rejects_garbage_path_without_car_prefix():
    """A 'path' that doesn't start with `/carpicture` is suspicious — the
    encar API has never returned those. Return None rather than producing
    a broken image."""
    assert first_photo_url(["/totally/not/encar/123.jpg"]) is None
    assert first_photo_url(["/carpicture06/x/ok.jpg"]) is not None


# ── first_photo_proxy_src: full /img?src=... query string ──────────────


def test_proxy_src_uses_urlencode():
    url = "https://ci.encar.com/carpicture06/pic4206/42063010_042.jpg"
    expected = "/img?src=" + url  # colon/slashes not urlencoded by quote(safe="")
    # quote() with safe="" encodes /, :, etc. We just verify it round-trips.
    from urllib.parse import unquote
    out = first_photo_proxy_src([url])
    assert out is not None
    assert out.startswith("/img?src=")
    # Unquote and compare to original URL.
    assert unquote(out[len("/img?src="):]) == url


def test_proxy_src_none_when_no_photo():
    assert first_photo_proxy_src(None) is None
    assert first_photo_proxy_src([]) is None


def test_proxy_src_normalizes_img_host_to_ci():
    """Even legacy img.encar.com URLs should appear in /img?src= as
    ci.encar.com — keeps the URLs in the HTML clean and avoids the
    proxy having to do extra rewrites (still does, as a safety net)."""
    out = first_photo_proxy_src(["https://img.encar.com/x/1.jpg"])
    assert out is not None
    assert "ci.encar.com" in out
    assert "img.encar.com" not in out
