"""Helpers for working with the `photo_urls` column on `Car`.

The column has been populated by three different code paths over the
project's history, so its contents are not uniform across rows:

1. **Current shape** (post-2026-06-20 fix): a JSON array of full
   ``https://ci.encar.com/...`` strings.
2. **Intermediate shape**: array of ``https://img.encar.com/...`` strings
   (before we learned the working host is ci, not img).
3. **Legacy shape**: array of bare paths like ``/carpicture06/...`` —
   from the very first version of the parser that didn't prepend any
   host.

The extractor here accepts all three (and a few forward-compat shapes
just in case) and returns a single absolute URL on the working CDN, or
``None`` if no usable entry exists. The web viewer's template renders
``None`` as a "no photo" placeholder rather than a broken image.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

# Working photo CDN. img.encar.com is filtered on many networks and is
# kept out of generated URLs even when the source data still references
# it (legacy rows).
PHOTO_CDN = "https://ci.encar.com"


def _coerce_entry(entry: Any) -> str | None:
    """Pull a URL-like string out of one photo_urls entry.

    Handles plain strings (full URL or bare path) and dicts with `path`
    or `url` keys (forward-compat). Returns ``None`` for anything that
    doesn't look like a usable photo URL.
    """
    if isinstance(entry, str):
        s = entry.strip()
        return s or None
    if isinstance(entry, dict):
        # Prefer `path` (matches the encar API shape); fall back to `url`.
        for key in ("path", "url"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _normalize_to_cdn(raw: str) -> str | None:
    """Turn whatever string the entry yielded into an absolute URL on
    PHOTO_CDN. Returns None for inputs that don't look like encar
    photo paths at all (defensive — better to show a placeholder than
    a 404 image)."""
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        # Already absolute. If it's on img.encar.com, swap the host.
        if raw.startswith("https://img.encar.com"):
            return PHOTO_CDN + raw[len("https://img.encar.com"):]
        if raw.startswith("http://img.encar.com"):
            return PHOTO_CDN + raw[len("http://img.encar.com"):]
        # Any other absolute URL is returned as-is (proxy will allowlist
        # only ci/img hosts; non-encar URLs would 404 there, but the
        # web viewer shouldn't be in the business of rewording third
        # parties' URLs).
        return raw
    if raw.startswith("/"):
        # Bare path from the legacy shape. The encar photo CDN only
        # ever serves /carpicture*/... — anything else is a misparse.
        if not raw.startswith("/carpicture"):
            return None
        return f"{PHOTO_CDN}{raw}"
    return None  # garbage like "carpicture06/x.jpg" without a leading slash


def first_photo_url(photo_urls: Any) -> str | None:
    """Return the first usable photo URL from a `photo_urls` value, or
    ``None`` if no entry is usable.

    The function is tolerant: it walks the list, normalizes each entry,
    and returns the first one that yields a non-None normalized URL.
    An empty / None input returns None. Non-list inputs (defensive
    against bad DB rows) also return None.
    """
    if not photo_urls or not isinstance(photo_urls, list):
        return None
    for entry in photo_urls:
        raw = _coerce_entry(entry)
        if raw is None:
            continue
        url = _normalize_to_cdn(raw)
        if url is not None:
            return url
    return None


def first_photo_proxy_src(photo_urls: Any) -> str | None:
    """Return a ``/img?src=<urlencoded URL>`` query string for the first
    photo, ready to drop into an ``<img src=...>`` attribute. Returns
    ``None`` when there's no usable photo so the template can render a
    placeholder."""
    url = first_photo_url(photo_urls)
    if url is None:
        return None
    # safe="" → encode /, :, ? etc. The proxy will urldecode on its end.
    return f"/img?src={quote(url, safe='')}"
