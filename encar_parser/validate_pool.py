"""Validate models.yaml pool — re-check Count for every enabled model.

Encar occasionally renames models (e.g. "쏘나타" → "쏘나타 더 뉴"), and
brand_label/family_label additions in the catalog can break the existing
raw_q. Run this after editing models.yaml or as a periodic check; it
prints a report and (with --disable) flips the affected entries to
enabled=false so the next live run won't fetch an empty list.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from pathlib import Path

import yaml

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError


async def _probe_count(raw_q: str) -> int | None:
    encoded = urllib.parse.quote(raw_q, safe="()._,")
    url = (
        f"https://api.encar.com/search/car/list/general?"
        f"count=true&q={encoded}&sr=%7CModifiedDate%7C0%7C5"
    )
    async with ApiFetcher() as api:
        try:
            resp = await api.get(url, referer="https://www.encar.com/")
            payload = resp.json()
            cnt = payload.get("Count")
            return int(cnt) if isinstance(cnt, int) else None
        except FetcherError:
            return None
        except Exception:
            return None


def _load_models(yaml_path: Path) -> list[dict]:
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["models"]


def _save_models(yaml_path: Path, models: list[dict]) -> None:
    yaml_path.write_text(
        yaml.safe_dump(
            {"models": models}, allow_unicode=True, sort_keys=False, width=120
        ),
        encoding="utf-8",
    )


async def _check_all(
    enabled: list[dict], concurrency: int
) -> list[tuple[dict, int | None]]:
    sem = asyncio.Semaphore(concurrency)

    async def bounded(m: dict) -> tuple[dict, int | None]:
        async with sem:
            raw_q = m.get("raw_q") or ""
            cnt = await _probe_count(raw_q) if raw_q else None
            return m, cnt

    return await asyncio.gather(*(bounded(m) for m in enabled))


def run_validate_pool(config: Path, *, disable: bool, concurrency: int) -> None:
    """Probe every enabled model; report anomalies; optionally disable broken ones."""
    models = _load_models(config)
    enabled = [m for m in models if m.get("enabled")]
    print(f"Loaded {len(models)} models ({len(enabled)} enabled) from {config}")

    results = asyncio.run(_check_all(enabled, concurrency))

    zero: list[tuple[dict, int | None]] = []
    nonzero: list[tuple[dict, int | None]] = []
    for m, cnt in results:
        if cnt is None or cnt == 0:
            zero.append((m, cnt))
        else:
            nonzero.append((m, cnt))

    print(f"\nResults: {len(nonzero)} OK, {len(zero)} zero/error\n")
    if zero:
        print("=== Zero / Error (need attention) ===")
        for m, cnt in zero:
            note = "(error)" if cnt is None else f"count={cnt}"
            print(f"  {m['slug']}: {note}  raw_q={m.get('raw_q', '')[:80]}...")
    if nonzero:
        print("\n=== OK ===")
        for m, cnt in nonzero:
            print(f"  {m['slug']}: count={cnt}")

    if disable and zero:
        print(f"\nDisabling {len(zero)} models with count=0...")
        zero_slugs = {m["slug"] for m, _ in zero}
        for m in models:
            if m["slug"] in zero_slugs and m.get("enabled"):
                m["enabled"] = False
                m["note"] = (
                    m.get("note", "")
                    + " | disabled by validate-pool (count=0)"
                ).strip(" |")
        _save_models(config, models)
        print(f"Patched {config}.")
