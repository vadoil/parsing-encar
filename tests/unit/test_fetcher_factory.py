import pytest

from encar_parser.fetchers.base import FetcherError
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.fetchers.api import ApiFetcher


@pytest.mark.asyncio
async def test_fallback_uses_api_on_success():
    """When ApiFetcher returns 200, no fallback to browser."""
    primary = ApiFetcher()
    secondary = ApiFetcher()  # will not be called

    call_count = {"primary": 0, "secondary": 0}

    class CountingApi(ApiFetcher):
        async def get(self, url, **kwargs):
            call_count["primary"] += 1
            from encar_parser.fetchers.base import FetcherResponse
            return FetcherResponse(url=url, body=b"{}", status=200)

        async def close(self): pass

    class CountingSecondary(CountingApi):
        async def get(self, url, **kwargs):
            call_count["secondary"] += 1
            from encar_parser.fetchers.base import FetcherResponse
            return FetcherResponse(url=url, body=b"{}", status=200)

        async def close(self): pass

    ff = FallbackFetcher(primary=CountingApi(), secondary=CountingSecondary())
    try:
        resp = await ff.get("https://example.com")
    finally:
        await ff.close()
    assert resp.status == 200
    assert call_count["primary"] == 1
    assert call_count["secondary"] == 0


@pytest.mark.asyncio
async def test_fallback_falls_back_on_403():
    """When primary raises FetcherError with 403, try secondary."""
    from encar_parser.fetchers.base import FetcherResponse

    class FailingPrimary(ApiFetcher):
        async def get(self, url, **kwargs):
            raise FetcherError("blocked", url=url, status=403)
        async def close(self): pass

    class OkSecondary(ApiFetcher):
        async def get(self, url, **kwargs):
            return FetcherResponse(url=url, body=b"{}", status=200)
        async def close(self): pass

    ff = FallbackFetcher(primary=FailingPrimary(), secondary=OkSecondary())
    try:
        resp = await ff.get("https://example.com")
    finally:
        await ff.close()
    assert resp.status == 200
