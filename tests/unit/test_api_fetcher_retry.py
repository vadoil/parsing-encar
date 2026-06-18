"""Retry-behavior tests for ApiFetcher.

Strategy: override Settings so retries are instant (min_wait=0, max_wait=0)
without touching the real config module. We monkey-patch the cached settings
object that ApiFetcher captured at __init__.
"""

import httpx
import pytest
import respx

from encar_parser.config import get_settings, reset_settings
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Force retries to be instant so tests run in milliseconds."""
    settings = get_settings()
    monkeypatch.setattr(settings, "retry_max_attempts", 3)
    monkeypatch.setattr(settings, "retry_min_wait_sec", 0.0)
    monkeypatch.setattr(settings, "retry_max_wait_sec", 0.0)
    yield
    reset_settings()


@pytest.mark.asyncio
@respx.mock
async def test_429_then_200_succeeds_after_retry():
    """First call → 429, second call → 200. ApiFetcher should return 200."""
    route = respx.get("https://api.encar.com/x").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with ApiFetcher() as f:
        resp = await f.get("https://api.encar.com/x")

    assert resp.status == 200
    assert resp.json() == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_constant_429_raises_after_max_attempts():
    """All calls return 429. After N attempts ApiFetcher raises FetcherError."""
    route = respx.get("https://api.encar.com/x").mock(return_value=httpx.Response(429))

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/x")

    assert exc_info.value.status == 429
    assert "Retries exhausted" in str(exc_info.value)
    assert route.call_count == 3  # max_attempts


@pytest.mark.asyncio
@respx.mock
async def test_403_does_not_retry():
    """403 is a bot-block — raise FetcherError immediately, no retry."""
    route = respx.get("https://api.encar.com/x").mock(return_value=httpx.Response(403))

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/x")

    assert exc_info.value.status == 403
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_5xx_retries_then_raises():
    """500s trigger retries; after exhaustion raise FetcherError."""
    route = respx.get("https://api.encar.com/x").mock(return_value=httpx.Response(500))

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/x")

    assert exc_info.value.status == 500
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_5xx_then_200_recovers():
    """500, 500, 200 → recovered."""
    route = respx.get("https://api.encar.com/x").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"recovered": True}),
        ]
    )

    async with ApiFetcher() as f:
        resp = await f.get("https://api.encar.com/x")

    assert resp.status == 200
    assert resp.json() == {"recovered": True}
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_timeout_retries_then_raises():
    """httpx.TimeoutException → retryable → eventually FetcherError."""
    route = respx.get("https://api.encar.com/x").mock(side_effect=httpx.TimeoutException("boom"))

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/x")

    assert "Retries exhausted" in str(exc_info.value)
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_timeout_then_200_recovers():
    """First call times out, second succeeds."""
    route = respx.get("https://api.encar.com/x").mock(
        side_effect=[
            httpx.TimeoutException("slow"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with ApiFetcher() as f:
        resp = await f.get("https://api.encar.com/x")

    assert resp.status == 200
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_404_does_not_retry():
    """4xx other than 403/429 is a logic error — no retry."""
    route = respx.get("https://api.encar.com/missing").mock(return_value=httpx.Response(404))

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/missing")

    assert exc_info.value.status == 404
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_retries():
    """Generic httpx network errors are retryable."""
    route = respx.get("https://api.encar.com/x").mock(
        side_effect=httpx.ConnectError("refused")
    )

    async with ApiFetcher() as f:
        with pytest.raises(FetcherError):
            await f.get("https://api.encar.com/x")

    assert route.call_count == 3
