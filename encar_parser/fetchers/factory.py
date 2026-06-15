"""Factory: combine a primary and a fallback fetcher."""

from __future__ import annotations

from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse


class FallbackFetcher:
    """Try primary first; on FetcherError with 403/429/timeout, use secondary.

    Other errors propagate (no fallback for 4xx, parse errors, etc.).
    """

    FALLBACK_STATUSES = {403, 429}

    def __init__(self, primary: Fetcher, secondary: Fetcher) -> None:
        self._primary = primary
        self._secondary = secondary

    async def get(self, url: str, **kwargs) -> FetcherResponse:
        try:
            return await self._primary.get(url, **kwargs)
        except FetcherError as e:
            if e.status in self.FALLBACK_STATUSES or e.status is None:
                return await self._secondary.get(url, **kwargs)
            raise

    async def close(self) -> None:
        await self._primary.close()
        await self._secondary.close()
