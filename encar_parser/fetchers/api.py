"""HTTP fetcher using httpx. Primary fetcher for the incar parser."""

from __future__ import annotations

import random

import httpx

from encar_parser.config import get_settings
from encar_parser.fetchers.base import Fetcher, FetcherError, FetcherResponse
from encar_parser.utils.ua import USER_AGENTS


class ApiFetcher:
    """Fetches URLs via httpx with rotation of User-Agent and retry-safe headers."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._ua_pool = list(USER_AGENTS)

    async def __aenter__(self) -> "ApiFetcher":
        timeout = httpx.Timeout(self._settings.request_timeout_sec)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _next_ua(self) -> str:
        return random.choice(self._ua_pool)

    async def get(
        self, url: str, *, params: dict | None = None, referer: str | None = None
    ) -> FetcherResponse:
        if self._client is None:
            raise RuntimeError("ApiFetcher used outside `async with` context")

        headers = {
            "User-Agent": self._next_ua(),
        }
        if referer:
            headers["Referer"] = referer

        try:
            resp = await self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise FetcherError(f"Timeout: {e}", url=url) from e
        except httpx.HTTPError as e:
            raise FetcherError(f"HTTP error: {e}", url=url) from e

        if resp.status_code in (403, 429):
            raise FetcherError(
                f"Blocked: {resp.status_code}",
                url=url,
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise FetcherError(
                f"HTTP {resp.status_code}",
                url=url,
                status=resp.status_code,
            )

        return FetcherResponse(
            url=str(resp.url),
            body=resp.content,
            status=resp.status_code,
            headers=dict(resp.headers),
        )
