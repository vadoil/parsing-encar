"""One-off test parse: 5 specific models, max_pages=1 each (~20 cars/model).

Manual run only — not a CLI command. Used to validate Phase 0/1 changes
(encar-field-map.md, accident_report_available) on a fresh ~100-car sample
before the full backfill.

Run: uv run python scripts/test_parse_5_models.py
"""
from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime

from sqlalchemy import select

from encar_parser.config import get_settings
from encar_parser.db.models import CarModelMatch, Run, SearchModel
from encar_parser.db.session import get_sessionmaker
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.browser import BrowserFetcher
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.pipeline import make_list_url_for_page, run_model
from encar_parser.utils.rate_limit import RandomDelay

SLUGS = [
    "avante-cn7",     # Hyundai (domestic, big listing)
    "g80-rg3",        # Genesis (domestic, small)
    "a6-c8",          # Audi (import, new candidate)
    "x5-g05",         # BMW (import, control — already parsed 30)
    "palisade-lx3",   # Hyundai (domestic, big SUV)
]
MAX_PAGES = 1  # ≈20 cars per model → ~100 total


async def main() -> None:
    settings = get_settings()
    Session = get_sessionmaker()

    async with Session() as session:
        # Resolve slugs → SearchModel rows
        result = await session.scalars(
            select(SearchModel).where(SearchModel.slug.in_(SLUGS))
        )
        models = list(result.all())
        found = {m.slug for m in models}
        if missing := set(SLUGS) - found:
            raise SystemExit(f"missing slugs in DB: {missing}")
        print(f"Found {len(models)} models: {sorted(found)}")

        # Build fetcher (api + browser fallback if available)
        async with ApiFetcher() as api:
            browser: BrowserFetcher | None = None
            try:
                browser = BrowserFetcher()
                await browser.__aenter__()
            except Exception as e:
                print(f"[warn] browser unavailable: {e}")
                browser = None

            if browser is not None:
                fetcher = FallbackFetcher(primary=api, secondary=browser)
            else:
                fetcher = api

            delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)
            run_record = Run(
                started_at=datetime.now(UTC),
                models_planned=len(models),
                models_done=0,
                cars_fetched=0,
                cars_failed=0,
                error_log=[],
            )
            session.add(run_record)
            await session.commit()
            await session.refresh(run_record)

            try:
                for sm in models:
                    try:
                        count = await run_model(
                            sm,
                            fetcher=fetcher,
                            session=session,
                            list_url_for_page=lambda page, action=sm.encar_action: make_list_url_for_page(
                                action, page
                            ),
                            detail_url_template=settings.api_detail_template,
                            request_delay=delay,
                            max_pages=MAX_PAGES,
                        )
                        run_record.models_done += 1
                        run_record.cars_fetched += count
                        print(f"  {sm.slug}: +{count} cars")
                    except Exception as e:
                        run_record.cars_failed += 1
                        print(f"  {sm.slug}: FAILED {e!r}")
                        if run_record.error_log is None:
                            run_record.error_log = []
                        run_record.error_log.append(
                            {"slug": sm.slug, "error": str(e)}
                        )
                    await session.commit()
                    await asyncio.sleep(random.uniform(
                        settings.min_model_delay_sec, settings.max_model_delay_sec
                    ))
            finally:
                if browser is not None:
                    await browser.__aexit__(None, None, None)

            run_record.finished_at = datetime.now(UTC)
            await session.commit()

        # Per-model breakdown: cars linked via car_model_matches
        print()
        print("=== Per-model totals (via car_model_matches) ===")
        for sm in models:
            n = await session.scalar(
                select(CarModelMatch.encar_id).where(CarModelMatch.search_model_id == sm.id)
            )
            # count distinct encar_ids for this model
            from sqlalchemy import func
            count = await session.scalar(
                select(func.count(func.distinct(CarModelMatch.encar_id)))
                .where(CarModelMatch.search_model_id == sm.id)
            )
            print(f"  {sm.slug}: {count} cars")

        duration = (run_record.finished_at - run_record.started_at).total_seconds()
        print()
        print(f"Run #{run_record.id}: {run_record.cars_fetched} fetched, "
              f"{run_record.cars_failed} failed, {duration:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())