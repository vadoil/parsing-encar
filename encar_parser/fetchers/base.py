"""Abstract fetcher interface. All fetchers (api, browser) implement this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class FetcherResponse:
    """A response from a fetcher."""

    url: str
    body: bytes
    status: int
    headers: dict[str, str] | None = None

    def json(self) -> object:
        """Parse body as JSON. Raises on invalid JSON."""
        import json
        return json.loads(self.body)

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


class FetcherError(Exception):
    """Base exception for fetcher errors."""

    def __init__(self, message: str, *, url: str | None = None, status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


@runtime_checkable
class Fetcher(Protocol):
    """Protocol every fetcher must implement."""

    async def get(self, url: str, *, params: dict | None = None) -> FetcherResponse:
        """Fetch a URL. Returns the raw response. Raises FetcherError on failure."""
        ...

    async def close(self) -> None:
        """Release resources (HTTP client, browser, etc.)."""
        ...
