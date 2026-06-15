"""End-to-end test that hits the real encar.com. Marked as @pytest.mark.live.

Run with: uv run pytest tests/e2e -m live
Skip by default in CI.
"""

import pytest

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.parsers.list_page import parse_search_list


@pytest.mark.asyncio
@pytest.mark.live
async def test_smoke_fetch_first_page_bmw_x5():
    """Fetch the first page of BMW X5 listings and verify the parser works."""
    from encar_parser.encar_url import ModelConfig, build_url

    cfg = ModelConfig(
        slug="bmw-x5-g05",
        name="BMW X5 (G05)",
        manufacturer="BMW",
        model_group="X5",
        model="X5 (G05)",
        year_from=2018,
    )
    url = build_url(cfg)

    async with ApiFetcher() as f:
        resp = await f.get(url)
        assert resp.status == 200
        items = parse_search_list(resp.json())
        assert len(items) > 0, "Expected at least one car on first page"
        assert items[0].encar_id > 0
