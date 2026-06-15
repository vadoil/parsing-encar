import httpx
import pytest
import respx

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_get_returns_response():
    respx.get("https://api.encar.com/search").mock(
        return_value=httpx.Response(200, json={"SearchResults": []})
    )
    async with ApiFetcher() as f:
        resp = await f.get("https://api.encar.com/search")
        assert resp.status == 200
        assert resp.json() == {"SearchResults": []}


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_raises_on_4xx():
    respx.get("https://api.encar.com/missing").mock(return_value=httpx.Response(404))
    async with ApiFetcher() as f:
        with pytest.raises(FetcherError) as exc_info:
            await f.get("https://api.encar.com/missing")
        assert exc_info.value.status == 404


@pytest.mark.asyncio
@respx.mock
async def test_api_fetcher_sends_user_agent_and_referer():
    route = respx.get("https://api.encar.com/x").mock(return_value=httpx.Response(200, json={}))
    async with ApiFetcher() as f:
        await f.get("https://api.encar.com/x", referer="https://www.encar.com/")
        sent = route.calls.last.request
        assert "User-Agent" in sent.headers
        assert sent.headers.get("referer") == "https://www.encar.com/"
