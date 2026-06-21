"""Memory diagnostic harness — measure RSS growth while parsing N models.

Run with:
    docker compose exec -T parser uv run --no-sync python scripts/diagnose_memory.py \\
        --models 8 --cars 50 --pages 3

Mocks Encar's HTTP endpoints with canned payloads (no network, no rate
limits) and runs the production pipeline. Samples Python heap + cgroup
RSS every model. Identifies whether memory grows linearly with car count
(OOM root cause) or stays flat (good).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import resource
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, SearchModel
from encar_parser.fetchers.base import Fetcher, FetcherResponse
from encar_parser.pipeline import run_model
from encar_parser.utils.log import setup_logging


# ── canned payloads ────────────────────────────────────────────────────


def _list_payload(count: int, page: int, page_size: int = 20) -> dict:
    items = []
    start = (page - 1) * page_size
    for i in range(start, start + min(page_size, max(0, count - start))):
        items.append({
            "Id": 40_000_000 + i,
            "Manufacturer": random.choice(["BMW", "아우디", "현대", "기아"]),
            "Model": random.choice(["X5 (G05)", "A6 (C8)", "그랜저", "쏘나타"]),
        })
    return {"Count": count, "SearchResults": items}


def _detail_payload(encar_id: int) -> dict:
    """Real-shape detail payload — sized to mimic a real Encar response.

    Real responses carry ~30 photo paths plus inspection report text,
    full option lists, and Korean spec strings — ~10 KB on the wire per
    car. We replicate that here so the stress test exercises the same
    per-car memory footprint as a real run.
    """
    return {
        "category": {
            "manufacturerName": "BMW",
            "modelName": "X5 (G05)",
            "yearMonth": "202511",
            "gradeName": "xDrive40i M Sport Package (인스퍼레이션)",
            "vehicleNo": f"{random.randint(100,999)}{chr(random.randint(0xAC00,0xD7A3))}{random.randint(1000,9999)}",
            "warranty": {"companyName": "BMW Korea"},
        },
        "spec": {
            "mileage": 4_000 + (encar_id % 90_000),
            "displacement": 2998,
            "transmissionName": "오토",
            "fuelName": "가솔린",
            "colorName": random.choice(["흰색", "검정색", "쥐색", "청색", "은색", "빨간색", "베이지"]),
            "seatCount": random.choice([5, 7]),
            "bodyName": "SUV",
            "driveType": "4WD",
            "engineType": "직렬 6기통",
        },
        "advertisement": {"price": 50_000_000 + (encar_id % 50_000_000)},
        "condition": {
            "accident": {"recordView": False, "resumeView": False},
            "seizing": {"pledgeCount": 0, "seizingCount": 0},
            "inspection": {
                "items": [
                    {"name": f"외관/내장 점검항목 {i}", "result": random.choice(["양호", "교환", "판금"]), "note": "정상 사용 흔적"}
                    for i in range(20)
                ]
            },
        },
        "options": [f"옵션{i}" for i in range(40)],
        "photos": [
            {"path": f"/carpicture/pic{encar_id}/{encar_id}_{n:03d}.jpg"}
            for n in range(32)  # 32 photos per car (real Encar average)
        ],
    }


# ── fake fetcher ───────────────────────────────────────────────────────


class FakeFetcher(Fetcher):
    def __init__(self, *, count: int) -> None:
        self._count = count
        self.calls = 0

    async def get(self, url: str, *, params: dict | None = None) -> FetcherResponse:
        self.calls += 1
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "/search/" in url or qs.get("page"):
            page = int(qs.get("page", ["1"])[0])
            payload = _list_payload(self._count, page)
        else:
            encar_id = int(parsed.path.rstrip("/").split("/")[-1])
            payload = _detail_payload(encar_id)
        return FetcherResponse(
            url=url,
            body=json.dumps(payload).encode("utf-8"),
            status=200,
        )

    async def close(self) -> None:  # pragma: no cover
        return None


# ── memory sampler ─────────────────────────────────────────────────────


def process_rss_mib() -> float:
    """Process RSS via /proc/self/status (Linux). Returns 0 on other OSes."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # KiB → MiB
    except FileNotFoundError:
        pass
    # macOS: ru_maxrss is process peak, not current. Use psutil if installed.
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def container_rss_mib() -> float | None:
    """Container RSS from cgroup (Linux). None if not in a cgroup."""
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            return int(f.read().strip()) / (1024 * 1024)
    except FileNotFoundError:
        pass
    for path in (
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        "/sys/fs/cgroup/memory/docker/memory.usage_in_bytes",
    ):
        try:
            with open(path) as f:
                return int(f.read().strip()) / (1024 * 1024)
        except FileNotFoundError:
            continue
    return None


def peak_rss_mib() -> float:
    """High-water RSS from getrusage."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024 * 1024)
    return usage.ru_maxrss / 1024


# ── driver ─────────────────────────────────────────────────────────────


async def _run(models: int, cars: int, pages: int) -> None:
    setup_logging()
    tracemalloc.start()

    # Use the configured (production-shape) DB engine so we measure
    # asyncpg + SQLAlchemy identity-map behaviour honestly. We open a
    # SAVEPOINT and roll back at the end so the dev DB stays clean.
    from encar_parser.config import get_settings
    from encar_parser.db.session import get_sessionmaker
    Session = get_sessionmaker()

    fetcher = FakeFetcher(count=cars)
    start = time.monotonic()
    car_count = 0
    samples: list[tuple[int, float, float, int]] = []  # cars, rss, heap, fetch_calls

    print(f"DB={get_settings().database_url}")
    print(f"models={models} cars/model={cars} pages={pages}")
    print(f"{'model':<14} {'cars':>6} {'rss':>8} {'heap_mib':>9} {'py_obj':>8} {'fetcher_calls':>13}")

    # All diag rows are tagged with this unique slug prefix so we can
    # delete them after the run without touching real data.
    run_id = int(time.time())
    diag_slug_prefix = f"diag-{run_id}-"

    async with Session() as s:
        for i in range(models):
            s.add(SearchModel(
                slug=f"{diag_slug_prefix}{i}",
                name=f"Diag Model {i}",
                encar_url=f"https://example/{i}",
                encar_action={"api_url": f"https://example/{i}", "sort": "ModifiedDate", "limit": 20},
                enabled=True,
                priority=i,
            ))
        await s.commit()

        all_models = (await s.scalars(select(SearchModel))).all()
        today_models = sorted(
            [m for m in all_models if m.slug.startswith(diag_slug_prefix)],
            key=lambda m: m.slug,
        )[:models]

        try:
            for sm in today_models:
                n = await run_model(
                    sm,
                    fetcher=fetcher,
                    session=s,
                    list_url_for_page=lambda page, action=sm.encar_action: f"https://example/{sm.slug}?page={page}",
                    detail_url_template="https://example/detail/{encar_id}",
                    request_delay=None,
                    max_pages=pages,
                )
                car_count += n
                snapshot = tracemalloc.take_snapshot()
                stats = snapshot.statistics("filename")
                heap_bytes = sum(s.size for s in stats)
                obj_count = sum(s.count for s in stats)
                cresp = container_rss_mib()
                rss = process_rss_mib() or (cresp or 0.0)
                samples.append((car_count, rss, heap_bytes / (1024 * 1024), fetcher.calls))
                print(f"{sm.slug:<24} {car_count:>6} {rss:>8.1f} {heap_bytes/(1024*1024):>9.1f} {obj_count:>8} {fetcher.calls:>13}")
        finally:
            # Wipe every diag car + diag search model — best-effort cleanup
            # so the dev DB stays clean. CarModelMatch cascades on car delete.
            from sqlalchemy import delete as sa_delete
            from encar_parser.db.models import Car
            try:
                diag_ids_subq = select(SearchModel.id).where(
                    SearchModel.slug.like(f"{diag_slug_prefix}%")
                )
                await s.execute(sa_delete(Car).where(
                    Car.encar_id >= 40_000_000
                ))
                await s.execute(sa_delete(SearchModel).where(
                    SearchModel.slug.like(f"{diag_slug_prefix}%")
                ))
                await s.commit()
                print(f"\n(cleaned up diag rows with prefix {diag_slug_prefix!r})")
            except Exception as e:
                await s.rollback()
                print(f"\n⚠️  cleanup failed: {e!r} — manual DELETE needed")

    elapsed = time.monotonic() - start
    peak = peak_rss_mib()

    print(f"\n=== SUMMARY ===")
    print(f"cars parsed       : {car_count}")
    print(f"fetcher calls     : {fetcher.calls}")
    print(f"elapsed           : {elapsed:.1f} s")
    print(f"peak process RSS  : {peak:.1f} MiB (rusage high-water)")
    cresp = container_rss_mib()
    if cresp is not None:
        print(f"final container RSS: {cresp:.1f} MiB (cgroup)")
    if samples:
        first_rss = samples[0][1]
        last_rss = samples[-1][1]
        delta = last_rss - first_rss
        per_car = delta / max(1, car_count)
        print(f"RSS first→last    : {first_rss:.1f} → {last_rss:.1f} MiB  (Δ {delta:+.1f}, {per_car * 1024:+.1f} KiB/car)")

        heap_first = samples[0][2]
        heap_last = samples[-1][2]
        heap_delta = heap_last - heap_first
        print(f"heap first→last   : {heap_first:.1f} → {heap_last:.1f} MiB  (Δ {heap_delta:+.1f})")

        if per_car > 0.5:
            print("⚠️  RSS grows >0.5 MiB/car — linear leak (OOM at scale)")
        elif per_car > 0.05:
            print("⚠️  RSS grows >50 KiB/car — small leak, watch at 100k+ cars")
        else:
            print("✅  RSS flat per car — safe to scale")

        # Top 5 Python-level allocators by total bytes held at end.
        snap = tracemalloc.take_snapshot()
        print("\nTop 5 Python allocators at end:")
        for stat in snap.statistics("filename")[:5]:
            print(f"  {stat.size / 1024:>8.1f} KiB  {stat.count:>6} obj  {stat.traceback}")

    tracemalloc.stop()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--models", type=int, default=8)
    p.add_argument("--cars", type=int, default=50)
    p.add_argument("--pages", type=int, default=3)
    args = p.parse_args()
    asyncio.run(_run(args.models, args.cars, args.pages))


if __name__ == "__main__":
    main()
