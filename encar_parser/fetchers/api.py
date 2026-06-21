"""HTTP fetcher using httpx. Primary fetcher for the incar parser."""

from __future__ import annotations

import random

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from encar_parser.config import get_settings
from encar_parser.fetchers.base import FetcherError, FetcherResponse
from encar_parser.utils.ua import USER_AGENTS


class RetryableError(FetcherError):
    """Subclass of FetcherError that signals 'worth retrying'.

    Tenacity is configured to retry ONLY this class. A bare FetcherError
    (e.g. 403 bot block, 4xx logic error) propagates immediately so the
    FallbackFetcher can switch to the browser without delay.
    """


class ApiFetcher:
    """Fetches URLs via httpx with rotation of User-Agent and tenacity-backed retries.

    Retry policy (from Settings):
      - 429 (rate limit), 5xx (server error), httpx.TimeoutException → retry
        up to ``retry_max_attempts`` times with exponential backoff + jitter
        (``retry_min_wait_sec`` to ``retry_max_wait_sec`` seconds).
      - 403 (bot block) and other 4xx → no retry, raise FetcherError so
        FallbackFetcher can switch to Playwright.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._ua_pool = list(USER_AGENTS)

    async def __aenter__(self) -> ApiFetcher:
        timeout = httpx.Timeout(self._settings.request_timeout_sec)
        # Cap the connection pool. Default httpx keeps up to 100 keepalive
        # connections — a hard upper bound for the parser (one process, no
        # concurrency in the pipeline), which is enough for the rate-limit
        # ceiling (1200/hour) and bounds memory if the host kernel starts
        # pressuring the container.
        limits = httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ru;q=0.7",
                "Origin": "https://www.encar.com",
                "Referer": self._settings.encar_referer,
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

        # Build a tenacity retry controller. We construct it (instead of using
        # the @retry decorator) so tests can pass a controller with zero wait.
        controller = AsyncRetrying(
            stop=stop_after_attempt(self._settings.retry_max_attempts),
            wait=wait_random_exponential(
                multiplier=1,
                min=self._settings.retry_min_wait_sec,
                max=self._settings.retry_max_wait_sec,
            ),
            retry=retry_if_exception_type(RetryableError),
            reraise=True,
        )

        try:
            async for attempt in controller:
                with attempt:
                    return await self._do_get(url, params, headers)
        except RetryableError as e:
            # Exhausted retries — surface as a regular FetcherError so
            # FallbackFetcher can decide what to do (typically: switch to
            # browser, or bubble up if browser is also configured to error).
            raise FetcherError(
                f"Retries exhausted: {e}",
                url=url,
                status=e.status,
            ) from e
        # Unreachable: controller.reraise=True + the inner return path means
        # either we returned or an exception was re-raised.
        raise RuntimeError("ApiFetcher.get: control flow invariant broken")

    async def _do_get(
        self, url: str, params: dict | None, headers: dict
    ) -> FetcherResponse:
        """Single attempt. Raises RetryableError or FetcherError."""
        assert self._client is not None
        try:
            resp = await self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise RetryableError(f"Timeout: {e}", url=url) from e
        except httpx.HTTPError as e:
            # Network-level errors (connection reset, DNS, etc) — retry.
            raise RetryableError(f"HTTP error: {e}", url=url) from e

        if resp.status_code == 403:
            # Bot block — no retry. FallbackFetcher will switch to Playwright.
            raise FetcherError(
                f"Blocked: {resp.status_code}",
                url=url,
                status=resp.status_code,
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            # Rate-limited or server error — retry.
            raise RetryableError(
                f"Retryable HTTP {resp.status_code}",
                url=url,
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            # Other 4xx — no retry, this is a real error.
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
