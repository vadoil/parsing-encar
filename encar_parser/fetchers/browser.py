"""Browser fetcher using Playwright. Used as fallback when ApiFetcher is blocked."""

from __future__ import annotations

import random

from playwright.async_api import async_playwright, Browser, BrowserContext

from encar_parser.config import get_settings
from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse
from encar_parser.utils.ua import USER_AGENTS


class BrowserFetcher:
    """Fetches URLs via headless Chromium. Slower but bypasses many bot checks."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserFetcher":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.headless_browser,
        )
        self._context = await self._browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="ko-KR",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def get(self, url: str, *, params: dict | None = None) -> FetcherResponse:
        if self._context is None:
            raise RuntimeError("BrowserFetcher used outside `async with` context")

        try:
            page = await self._context.new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for results to render
                await page.wait_for_load_state("networkidle", timeout=15000)
                body = await page.content()
                status = response.status if response else 0
            finally:
                await page.close()
        except Exception as e:
            raise FetcherError(f"Browser error: {e}", url=url) from e

        if status == 403 or status == 429:
            raise FetcherError(f"Blocked: {status}", url=url, status=status)
        if status >= 400:
            raise FetcherError(f"HTTP {status}", url=url, status=status)

        return FetcherResponse(url=url, body=body.encode("utf-8"), status=status)
