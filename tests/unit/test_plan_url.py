"""Regression tests for ``plan --probe`` URL construction.

The pre-fix bug: ``_force_small_limit`` manually quoted ``sr`` with
``urllib.parse.quote`` (producing ``%7CModifiedDate%7C0%7C5``) then ran
the result through ``urllib.parse.urlencode`` — which percent-encoded
the ``%`` characters AGAIN, yielding ``%257CModifiedDate%257C0%257C5``.
Encar rejects that with HTTP 400.

These tests assert that ``sr`` is single-encoded (``%7C`` not ``%257C``)
in the final URL.
"""
from __future__ import annotations

import urllib.parse

import httpx
import pytest
import respx

from encar_parser.db.models import SearchModel
from encar_parser.plan import _force_small_limit, probe_live_counts


def _mk(slug: str, *, api_url: str) -> SearchModel:
    return SearchModel(
        slug=slug,
        name=slug,
        encar_url="",
        encar_action={"api_url": api_url, "sort": "ModifiedDate", "limit": 20},
        priority=100,
    )


# ── URL-shape regression test ──────────────────────────────────────────


def test_force_small_limit_does_not_double_encode_sr():
    """The hot fix: sr must be %7C, never %257C."""
    src = (
        "https://api.encar.com/search/car/list/general?"
        "count=true&q=(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.Model.X5.))."
        "Year.range(202001..202612).)&sr=%7CModifiedDate%7C0%7C20"
    )
    out = _force_small_limit(src)
    parsed = urllib.parse.urlparse(out)
    qs = urllib.parse.parse_qs(parsed.query)
    # Single-encoded — %7C, NOT %257C.
    assert qs["sr"] == ["|ModifiedDate|0|5"], (
        f"sr must be single-encoded pipe sequence, got {qs['sr']!r}"
    )
    # Belt and braces: assert the raw URL string contains no %25 anywhere.
    assert "%25" not in out, f"double-encoded %% found in URL: {out}"


def test_force_small_limit_preserves_other_query_params():
    """q, count, and any other params pass through untouched."""
    src = (
        "https://api.encar.com/search/car/list/general?"
        "count=true&q=foo&sr=%7CModifiedDate%7C0%7C20&extra=bar"
    )
    out = _force_small_limit(src)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(out).query)
    assert qs["q"] == ["foo"]
    assert qs["count"] == ["true"]
    assert qs["extra"] == ["bar"]
    assert qs["sr"] == ["|ModifiedDate|0|5"]


def test_force_small_limit_no_sr_returns_unchanged():
    """If there's no sr param (shouldn't happen for real models), pass through."""
    src = "https://api.encar.com/search?q=foo"
    out = _force_small_limit(src)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(out).query)
    assert qs["q"] == ["foo"]
    assert "sr" not in qs


# ── end-to-end via respx — proves Encar would actually accept the URL ──


@pytest.mark.asyncio
@respx.mock
async def test_probe_live_counts_uses_single_encoded_url_and_returns_counts():
    """Mock the list endpoint; assert it was hit with sr=%7C… and counts > 0."""
    api_url = (
        "https://api.encar.com/search/car/list/general?"
        "count=true&q=BMW.X5.Year.range.202001.202612."
        "&sr=%7CModifiedDate%7C0%7C20"
    )
    m = _mk("bmw-x5-g05", api_url=api_url)

    route = respx.get("https://api.encar.com/search/car/list/general").mock(
        return_value=httpx.Response(200, json={"Count": 1042, "SearchResults": []})
    )

    counts = await probe_live_counts([m])

    assert counts == {"bmw-x5-g05": 1042}
    assert route.called
    # Capture the URL the fetcher actually sent — must contain %7C not %257C.
    last_request = respx.calls[0].request
    raw_url = str(last_request.url)
    assert "%7C" in raw_url, f"expected single-encoded %7C in URL: {raw_url}"
    assert "%257C" not in raw_url, f"double-encoded %257C found in URL: {raw_url}"


@pytest.mark.asyncio
@respx.mock
async def test_probe_live_counts_zero_count_returns_zero():
    m = _mk("empty", api_url="https://api.encar.com/search/car/list/general?count=true&q=x&sr=%7CModifiedDate%7C0%7C20")
    respx.get("https://api.encar.com/search/car/list/general").mock(
        return_value=httpx.Response(200, json={"Count": 0, "SearchResults": []})
    )
    counts = await probe_live_counts([m])
    assert counts == {"empty": 0}


@pytest.mark.asyncio
@respx.mock
async def test_probe_live_counts_http_error_returns_zero_not_raises():
    """A 400 from Encar must NOT crash the probe loop — record 0 and continue."""
    m1 = _mk("bad", api_url="https://api.encar.com/search/car/list/general?count=true&q=bad&sr=%7CModifiedDate%7C0%7C20")
    m2 = _mk("good", api_url="https://api.encar.com/search/car/list/general?count=true&q=good&sr=%7CModifiedDate%7C0%7C20")
    respx.get("https://api.encar.com/search/car/list/general").mock(
        side_effect=[
            httpx.Response(400, text="Bad Request"),
            httpx.Response(200, json={"Count": 50, "SearchResults": []}),
        ]
    )
    counts = await probe_live_counts([m1, m2])
    # The 400 is recorded as 0 (the whole ApiFetcher retries 3 times then raises
    # FetcherError; we treat that as "couldn't probe"). The 200 returns the real count.
    assert counts["bad"] == 0
    assert counts["good"] == 50
