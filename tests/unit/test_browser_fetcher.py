import pytest


@pytest.mark.asyncio
@pytest.mark.live
async def test_browser_fetcher_smoke():
    """Smoke test against real encar.com. Skipped by default, run with -m live."""
    from encar_parser.fetchers.browser import BrowserFetcher

    async with BrowserFetcher() as f:
        resp = await f.get("https://www.encar.com/fc/fc_carsearchlist.do?carType=for")
        assert resp.status == 200
        assert b"encar" in resp.body.lower() or len(resp.body) > 1000
