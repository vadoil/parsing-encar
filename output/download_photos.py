"""Download all photos referenced in _details.json to output/photos/{carid}/.

Run this from a machine where img.encar.com is reachable. After it finishes,
re-run `build_export.py` — it auto-detects locally-mirrored files and uses
relative `photos/...` paths in CSV/HTML instead of remote URLs.

Usage:
    .venv/bin/python output/download_photos.py
    .venv/bin/python output/build_export.py    # regenerate with local photos
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path

import httpx

HERE = Path(__file__).parent
DETAILS_PATH = HERE / "_details.json"
PHOTOS_DIR = HERE / "photos"

IMG_BASE = "https://img.encar.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.encar.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

CONCURRENCY = 8
TIMEOUT = httpx.Timeout(15.0, connect=8.0)


def url_for(path: str) -> str:
    return path if path.startswith("http") else f"{IMG_BASE}{path}"


async def download_one(client, sem, url, target):
    if target.exists() and target.stat().st_size > 0:
        return "skip", target
    async with sem:
        try:
            r = await client.get(url)
            if r.status_code == 200 and r.content:
                target.write_bytes(r.content)
                return "ok", target
            return f"http_{r.status_code}", None
        except Exception as e:
            return f"err:{type(e).__name__}", None


async def main() -> None:
    if not DETAILS_PATH.exists():
        raise SystemExit(f"Missing {DETAILS_PATH}")
    details = json.loads(DETAILS_PATH.read_text())
    print(f"Loaded {len(details)} detail responses")

    PHOTOS_DIR.mkdir(exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    counters = {"ok": 0, "skip": 0, "fail": 0}
    fail_examples: list[str] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        tasks: list = []
        for carid_str, payload in details.items():
            car_dir = PHOTOS_DIR / carid_str
            car_dir.mkdir(exist_ok=True)
            for p in payload.get("photos", []) or []:
                path = p.get("path", "")
                if not path:
                    continue
                url = url_for(path)
                fname = Path(urllib.parse.urlparse(url).path).name
                target = car_dir / fname
                tasks.append(download_one(client, sem, url, target))

        results = await asyncio.gather(*tasks)
        for status, _ in results:
            if status in ("ok", "skip"):
                counters[status] += 1
            else:
                counters["fail"] += 1
                if len(fail_examples) < 5:
                    fail_examples.append(status)

    total_bytes = sum(p.stat().st_size for p in PHOTOS_DIR.rglob("*") if p.is_file())
    print(f"ok={counters['ok']}  skip={counters['skip']}  fail={counters['fail']}")
    print(f"total disk: {total_bytes/1024/1024:.1f} MB across {len(list(PHOTOS_DIR.iterdir()))} cars")
    if fail_examples:
        print(f"sample failures: {fail_examples}")


if __name__ == "__main__":
    asyncio.run(main())