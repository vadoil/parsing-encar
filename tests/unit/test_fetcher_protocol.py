import pytest

from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse


@pytest.mark.asyncio
async def test_protocol_can_be_implemented():
    class MyFetcher:
        async def get(self, url: str) -> FetcherResponse:
            return FetcherResponse(url=url, body=b"hello", status=200)

        async def close(self) -> None:
            pass

    f: Fetcher = MyFetcher()
    resp = await f.get("https://example.com")
    assert resp.body == b"hello"
    assert resp.status == 200
    await f.close()


def test_fetcher_error_carries_url():
    err = FetcherError("boom", url="https://example.com", status=503)
    assert err.status == 503
    assert str(err) == "boom"
