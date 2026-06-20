"""Unit tests for the /img proxy used by the web viewer.

The proxy has one job: take a user-supplied `src` URL and return the image
bytes from the working encar photo CDN (ci.encar.com), with an allowlist so
it can't be turned into an open proxy. See encar_parser/web/img_proxy.py.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from encar_parser.web.img_proxy import (
    ProxyError,
    fetch_image,
    normalize_source_url,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clean_image_cache():
    """The proxy keeps a process-global TTL+LRU cache (one singleton for
    the test process). Reset it before every test so cached entries from a
    previous test don't make the next one skip its httpx call — which would
    surface as a respx "route not called" failure."""
    reset_cache()
    yield
    reset_cache()


# ── normalize_source_url: host swap + allowlist ────────────────────────


def test_normalize_swaps_img_to_ci_host():
    """The legacy img.encar.com host is filtered on many networks — swap to
    the working ci.encar.com CDN that serves the same paths."""
    out = normalize_source_url("https://img.encar.com/carpicture06/x/123_001.jpg")
    assert out == "https://ci.encar.com/carpicture06/x/123_001.jpg"


def test_normalize_passes_ci_host_through():
    """Already-on-ci URLs are returned unchanged."""
    out = normalize_source_url("https://ci.encar.com/carpicture06/x/123_001.jpg")
    assert out == "https://ci.encar.com/carpicture06/x/123_001.jpg"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/whatever.jpg",
        "http://localhost:8000/secret",
        "https://attacker.tld/probe?x=y",
        "ftp://ci.encar.com/file.jpg",       # wrong scheme
        "javascript:alert(1)",                # not even http
        "https://CI.ENCAR.COM/x.jpg",         # wrong case — must not match
    ],
)
def test_normalize_rejects_disallowed_hosts(url):
    """Anything not in {ci.encar.com, img.encar.com} (or wrong scheme / case)
    must raise so the route handler returns 404 — never an open proxy."""
    with pytest.raises(ProxyError):
        normalize_source_url(url)


def test_normalize_rejects_unparseable():
    with pytest.raises(ProxyError):
        normalize_source_url("not a url at all")


# ── fetch_image: real httpx call, mocked via respx ──────────────────────


@pytest.mark.asyncio
async def test_fetch_image_returns_bytes_and_content_type():
    url = "https://ci.encar.com/carpicture06/pic4206/42063010_042.jpg"
    fake_bytes = b"\xff\xd8\xff\xe0" + b"FAKE-JPEG-CONTENT" * 10
    with respx.mock(base_url="https://ci.encar.com") as mock:
        route = mock.get("/carpicture06/pic4206/42063010_042.jpg").mock(
            return_value=Response(
                200,
                content=fake_bytes,
                headers={"content-type": "image/jpeg"},
            )
        )
        data, content_type = await fetch_image(url)

    assert data == fake_bytes
    assert content_type == "image/jpeg"
    assert route.called
    # Required headers for ci.encar.com to serve us real bytes.
    sent = route.calls.last.request
    assert "Mozilla/5.0" in sent.headers["user-agent"]
    assert "encar.com" in sent.headers["referer"]


@pytest.mark.asyncio
async def test_fetch_image_passes_through_img_host_via_swap():
    """If the caller hands us an img.encar.com URL (legacy), we must swap
    to ci.encar.com before fetching."""
    img_url = "https://img.encar.com/carpicture06/x/9_042.jpg"
    with respx.mock() as mock:
        # No routes on ci.encar.com in this test — the proxy must hit it,
        # not the img.encar.com URL the caller provided.
        route = mock.get("https://ci.encar.com/carpicture06/x/9_042.jpg").mock(
            return_value=Response(200, content=b"OK", headers={"content-type": "image/jpeg"})
        )
        await fetch_image(img_url)
    assert route.called


@pytest.mark.asyncio
async def test_fetch_image_404_when_upstream_missing():
    with respx.mock(base_url="https://ci.encar.com") as mock:
        mock.get("/missing.jpg").mock(return_value=Response(404))
        with pytest.raises(ProxyError):
            await fetch_image("https://ci.encar.com/missing.jpg")


@pytest.mark.asyncio
async def test_fetch_image_caches_repeated_calls():
    """Second call for the same URL must NOT hit the network again."""
    url = "https://ci.encar.com/carpicture06/x/11_042.jpg"
    with respx.mock(base_url="https://ci.encar.com") as mock:
        route = mock.get("/carpicture06/x/11_042.jpg").mock(
            return_value=Response(200, content=b"x", headers={"content-type": "image/jpeg"})
        )
        await fetch_image(url)
        await fetch_image(url)
        await fetch_image(url)
    assert route.call_count == 1
