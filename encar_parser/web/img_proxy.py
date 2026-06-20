"""Image proxy used by the web viewer.

Why this exists
───────────────
The encar photo CDN has two hostnames that resolve to the *same* image
storage:

    https://img.encar.com/...   ← historically used by this project
    https://ci.encar.com/...    ← actual working CDN (verified 2026-06-20:
                                  img.encar.com returns SSL timeouts from
                                  our dev shell and is filtered on many
                                  user networks in RU)

The web viewer serves thumbnails through ``/img?src=<url>`` so that
browsers in RU can load encar-hosted photos without ever talking to
encar.com directly. Without the proxy, every user would need to reach
encar.com themselves; with the proxy, only the server does.

Security
────────
The proxy is NOT an open forwarder. ``normalize_source_url`` enforces:

* scheme must be http/https
* host must be in ``settings.img_proxy_allowed_hosts`` (lowercase compare)
* img.encar.com URLs are silently rewritten to ci.encar.com before fetch

Anything else raises :class:`ProxyError`, which the route handler turns
into a 404.

Caching
───────
A simple in-memory TTL+LRU cache. ``httpx`` calls are expensive (~200ms
for a 40KB JPEG), and the same handful of URLs will be requested dozens
of times per page render. The cache survives across requests but is
process-local — when the container restarts, the first hit warms it.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Final
from urllib.parse import urlparse

import httpx

from encar_parser.config import get_settings

# Headers we send upstream. ci.encar.com (like most Korean CDNs) refuses
# requests without a plausible User-Agent and an encar.com Referer.
USER_AGENT: Final = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
REFERER: Final = "http://www.encar.com/"


class ProxyError(Exception):
    """Raised when a src URL is rejected by the proxy (out of allowlist,
    wrong scheme, unparseable, upstream 4xx, etc.). Route handler maps
    this to HTTP 404."""


# ── Host swap + allowlist ──────────────────────────────────────────────


def normalize_source_url(src: str) -> str:
    """Validate `src` and (if needed) rewrite its host.

    Raises :class:`ProxyError` for anything not in the allowlist or with
    a non-http(s) scheme. Case-sensitive compare for the allowed hosts —
    ``CI.ENCAR.COM`` is rejected (consistent with how browsers normalize
    scheme/host in ``<img src>``, but stricter is safer).
    """
    if not src:
        raise ProxyError("empty src")
    try:
        parsed = urlparse(src)
    except Exception as e:
        raise ProxyError(f"unparseable src: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise ProxyError(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.netloc  # netloc = host[:port]
    allowed = get_settings().img_proxy_allowed_hosts
    if host not in allowed:
        raise ProxyError(f"host {host!r} not in allowlist")
    # Host swap: img → ci (preserves path, query, fragment).
    if host == "img.encar.com":
        return f"https://ci.encar.com{parsed.path}" + (
            f"?{parsed.query}" if parsed.query else ""
        ) + (f"#{parsed.fragment}" if parsed.fragment else "")
    return src


# ── In-process TTL+LRU cache ────────────────────────────────────────────


class _ImageCache:
    """Tiny TTL+LRU cache: maps URL → (bytes, content_type, stored_at).

    Evicts least-recently-used entry when max_entries is reached. An entry
    is also considered stale once `ttl_sec` has elapsed since `stored_at`.
    """

    def __init__(self, max_entries: int, ttl_sec: int) -> None:
        self.max_entries = max_entries
        self.ttl_sec = ttl_sec
        self._data: OrderedDict[str, tuple[bytes, str, float]] = OrderedDict()

    def get(self, key: str) -> tuple[bytes, str] | None:
        v = self._data.get(key)
        if v is None:
            return None
        data, ct, stored_at = v
        if time.monotonic() - stored_at > self.ttl_sec:
            self._data.pop(key, None)
            return None
        # Mark as recently used.
        self._data.move_to_end(key)
        return data, ct

    def put(self, key: str, value: tuple[bytes, str]) -> None:
        self._data[key] = (value[0], value[1], time.monotonic())
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


_cache: _ImageCache | None = None


def _get_cache() -> _ImageCache:
    global _cache
    if _cache is None:
        s = get_settings()
        _cache = _ImageCache(
            max_entries=s.img_proxy_cache_max_entries,
            ttl_sec=s.img_proxy_cache_ttl_sec,
        )
    return _cache


def reset_cache() -> None:
    """For tests: drop the singleton so settings changes are honoured."""
    global _cache
    _cache = None


# ── Fetch ──────────────────────────────────────────────────────────────


async def fetch_image(src: str) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for `src`.

    Cache lookup is by the *original* src URL (the cache key the browser
    sees in ``<img src=...>``); the actual HTTP request goes to the
    post-swap URL. That way, even if the page contains legacy
    ``img.encar.com`` URLs, the second hit doesn't re-fetch.
    """
    cache = _get_cache()
    cached = cache.get(src)
    if cached is not None:
        return cached

    upstream = normalize_source_url(src)
    timeout = httpx.Timeout(get_settings().img_proxy_timeout_sec)
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(upstream, headers=headers)
    if resp.status_code != 200 or not resp.content:
        raise ProxyError(f"upstream returned HTTP {resp.status_code}")
    content_type = resp.headers.get("content-type", "application/octet-stream")
    # Drop charset etc — we only need the MIME type for the browser.
    content_type = content_type.split(";", 1)[0].strip() or "application/octet-stream"
    value = (resp.content, content_type)
    cache.put(src, value)
    return value
