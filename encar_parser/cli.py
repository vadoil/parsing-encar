"""Typer CLI for the encar parser."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml
from sqlalchemy import select

from encar_parser.backfill import walk_backfill
from encar_parser.car_type import (
    CAR_TYPE_DOMESTIC,
    CAR_TYPE_IMPORT,
    DOMESTIC_BRANDS_EN_TO_KR,
    KNOWN_IMPORT_BRANDS,
    classify_brand,
    is_known_brand,
)
from encar_parser.config import get_settings
from encar_parser.db.models import Run, SearchModel
from encar_parser.db.repository import (
    get_enabled_models,
    upsert_search_model,
)
from encar_parser.db.session import get_sessionmaker
from encar_parser.dedup import run_dedup
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.browser import BrowserFetcher
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.memlog import MemSampler
from encar_parser.pipeline import (
    make_list_url_for_page,
    run_model,
    run_model_incremental,
)
from encar_parser.plan import run_plan_cli, render_plan_text, render_rotation_text
from encar_parser.scheduler import plan_today
from encar_parser.utils.log import get_logger, setup_logging
from encar_parser.utils.rate_limit import RandomDelay
from encar_parser.validate_pool import run_validate_pool

# Cap the per-run error_log size so a run with thousands of failures
# doesn't bloat the runs row in Postgres. We keep the first N and a
# tail count of suppressed errors.
MAX_ERROR_LOG_ENTRIES = 50

log = get_logger(__name__)
app = typer.Typer(help="Encar parser CLI")


@app.command()
def validate_pool(
    config: Path = typer.Option(Path("models.yaml"), "--config", "-c"),
    disable: bool = typer.Option(False, "--disable", help="Patch models.yaml: set enabled=false on Count=0 entries"),
    concurrency: int = typer.Option(4, "--concurrency", help="Parallel probes"),
) -> None:
    """Probe Count for every enabled model and report anomalies."""
    run_validate_pool(config, disable=disable, concurrency=concurrency)


def _load_models_yaml(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        typer.echo(f"models.yaml not found at {path}", err=True)
        raise typer.Exit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("models", [])


@app.command()
def sync(
    config_path: Path = typer.Option(Path("models.yaml"), "--config", "-c"),
) -> None:
    """Synchronize models.yaml into the database."""
    setup_logging()
    asyncio.run(_sync_async(config_path))


async def _sync_async(config_path: Path) -> None:
    items = _load_models_yaml(config_path)
    Session = get_sessionmaker()
    async with Session() as session:
        seen_slugs: set[str] = set()
        for item in items:
            cfg = build_url_from_item(item)
            await upsert_search_model(
                session,
                slug=cfg["slug"],
                name=cfg["name"],
                encar_url=cfg["encar_url"],
                encar_action=cfg["encar_action"],
                enabled=cfg.get("enabled", True),
                priority=cfg.get("priority", 100),
            )
            seen_slugs.add(cfg["slug"])
            log.info("model_synced", slug=cfg["slug"])

        # Disable models no longer in YAML
        result = await session.scalars(select(SearchModel))
        for sm in result.all():
            if sm.slug not in seen_slugs:
                sm.enabled = False
                log.info("model_disabled", slug=sm.slug)
        await session.commit()
    typer.echo(f"Synced {len(items)} models.")


def build_url_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a YAML model item to the fields needed for upsert."""
    from encar_parser.encar_url import ModelConfig, build_action
    from encar_parser.encar_url import build_url as _build_url
    cfg = ModelConfig(**{k: v for k, v in item.items() if k != "enabled"})
    return {
        "slug": item["slug"],
        "name": item["name"],
        "encar_url": _build_url(cfg),
        "encar_action": build_action(cfg),
        "enabled": item.get("enabled", True),
        "priority": item.get("priority", 100),
    }


@app.command()
def run(
    max_models: int = typer.Option(
        0, "--max-models",
        help="Cap the run to this many models (0 = all today's models). "
             "Used for memory-bounded test runs.",
    ),
    max_pages: int = typer.Option(
        0, "--max-pages",
        help="Override settings.max_pages for this run. Useful for fast "
             "test runs that just want a few pages per model.",
    ),
) -> None:
    """Run today's scheduled models."""
    setup_logging()
    asyncio.run(_run_async(max_models=max_models, max_pages=max_pages))


async def _run_async(*, max_models: int = 0, max_pages: int = 0) -> None:
    settings = get_settings()
    Session = get_sessionmaker()
    mem = MemSampler(interval_sec=60.0, label="run")
    mem.start()
    effective_max_pages = max_pages or settings.max_pages
    if max_pages:
        log.info("max_pages_override", before=settings.max_pages, after=max_pages)
    try:
        async with Session() as session:
            all_models = await get_enabled_models(session)
            today_models = models_for_today(all_models, datetime.now(UTC).date())
            if max_models and len(today_models) > max_models:
                log.info(
                    "max_models_cap",
                    before=len(today_models),
                    after=max_models,
                    hint="--max-models set; truncating today's slice",
                )
                today_models = today_models[:max_models]
            if not today_models:
                typer.echo("No models scheduled for today.")
                return

            run_record = Run(
                started_at=datetime.now(UTC),
                models_planned=len(today_models),
                models_done=0,
                cars_fetched=0,
                cars_failed=0,
                error_log=[],
            )
            session.add(run_record)
            await session.commit()
            await session.refresh(run_record)

            suppressed_errors = 0  # count beyond MAX_ERROR_LOG_ENTRIES

            async with ApiFetcher() as api:
                # BrowserFetcher is the fallback when ApiFetcher hits a 403/429.
                # Construct it lazily — if Playwright or Chromium isn't installed,
                # the run should still complete (with API-only fetches), not die
                # in the `async with` line.
                browser: BrowserFetcher | None = None
                try:
                    browser = BrowserFetcher()
                    await browser.__aenter__()
                except Exception as e:
                    log.warning(
                        "browser_fetcher_unavailable",
                        error=str(e),
                        hint="run `playwright install chromium --with-deps` to enable fallback",
                    )
                    browser = None

                if browser is not None:
                    fetcher: Fetcher = FallbackFetcher(primary=api, secondary=browser)
                else:
                    fetcher = api  # type: ignore[assignment]
                request_delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)

                try:
                    for sm in today_models:
                        try:
                            count = await run_model(
                                sm,
                                fetcher=fetcher,
                                session=session,
                                list_url_for_page=lambda page, action=sm.encar_action: make_list_url_for_page(action, page),  # type: ignore[misc]  # noqa: E501
                                detail_url_template=settings.api_detail_template,
                                request_delay=request_delay,
                                max_pages=effective_max_pages,
                            )
                            run_record.models_done += 1
                            run_record.cars_fetched += count
                        except Exception as e:
                            run_record.cars_failed += 1
                            log.error("model_failed", slug=sm.slug, error=str(e))
                            if run_record.error_log is None:
                                run_record.error_log = []
                            if len(run_record.error_log) < MAX_ERROR_LOG_ENTRIES:
                                run_record.error_log.append(
                                    {"slug": sm.slug, "error": str(e)}
                                )
                            else:
                                suppressed_errors += 1
                        # Drop everything the ORM still holds between models.
                        # Identity map uses weak refs so this is mostly a
                        # belt-and-braces measure, but it also releases any
                        # strongly-referenced Car objects (e.g. inside
                        # `run_record` or local closures).
                        session.expunge_all()
                        # Pause between models
                        await asyncio.sleep(random.uniform(
                            settings.min_model_delay_sec, settings.max_model_delay_sec
                        ))
                finally:
                    if browser is not None:
                        await browser.__aexit__(None, None, None)

            run_record.finished_at = datetime.now(UTC)
            await session.commit()

            # Replay dedup so freshly-added listings get folded into the
            # existing duplicate groups — and so any newcomer with a higher
            # ``encar_id`` correctly steals the primary slot from a row the
            # last pass marked primary.
            dedup_report = await run_dedup(session)
            await session.commit()
            log.info(
                "dedup_done",
                duplicate_groups=dedup_report.duplicate_groups,
                rows_hidden=dedup_report.rows_hidden,
                rows_primary=dedup_report.rows_primary,
            )

            summary = mem.stop()
            typer.echo(json.dumps({
                "run_id": run_record.id,
                "models_planned": run_record.models_planned,
                "models_done": run_record.models_done,
                "cars_fetched": run_record.cars_fetched,
                "cars_failed": run_record.cars_failed,
                "suppressed_errors": suppressed_errors,
                "dedup": dedup_report.as_dict(),
                "memory": summary.as_dict(),
            }, ensure_ascii=False, indent=2))
    finally:
        # If we exited via exception, mem.stop() wasn't called — flush now
        # so the peak still lands in encar.log.
        if mem._thread is not None and mem._thread.is_alive():
            mem.stop()


@app.command()
def migrate() -> None:
    """Run alembic migrations."""
    import subprocess
    setup_logging()
    result = subprocess.run(["alembic", "upgrade", "head"], check=False)
    raise typer.Exit(result.returncode)


@app.command()
def dedup(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text"),
) -> None:
    """Collapse duplicate listings: mark the freshest car in each group
    ``is_primary = True`` and hide the rest from the vitrine.

    A duplicate is detected by:

    1. (brand, model, year_month, mileage_km, color_original) all
       non-NULL and matching — the primary signal.
    2. Identical ``photo_urls`` sets — fallback for rows with missing
       key fields.

    Within each group the row with the largest ``encar_id`` wins
    (``is_primary = True``). The pass always recomputes from current
    data, so it's safe to re-run after every parse — newcomers with a
    higher ``encar_id`` correctly steal the primary slot from older
    duplicates.

    Idempotent: running twice in a row yields the same state.
    """
    setup_logging()
    asyncio.run(_dedup_async(json_output=json_output))


async def _dedup_async(*, json_output: bool) -> None:
    Session = get_sessionmaker()
    async with Session() as session:
        report = await run_dedup(session)
        await session.commit()
    payload = report.as_dict()
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            "dedup done — "
            f"duplicate_groups={payload['duplicate_groups']}  "
            f"rows_hidden={payload['rows_hidden']}  "
            f"rows_primary={payload['rows_primary']}"
        )


# ── Phase 4: scheduler (incremental / backfill / plan) ──────────────────


@app.command("run-incremental")
def run_incremental(
    cooldown_hours: int = typer.Option(
        0, "--cooldown-hours",
        help="Skip models whose last_run_at is within this window. "
             "0 = use settings.scheduler_cooldown_hours.",
    ),
    bucket_count: int = typer.Option(
        0, "--bucket-count",
        help="Override settings.scheduler_bucket_count (default 14).",
    ),
    max_models: int = typer.Option(
        0, "--max-models",
        help="Cap today's slice to this many models (0 = all).",
    ),
    max_pages: int = typer.Option(
        0, "--max-pages",
        help="Override settings.max_pages for this run.",
    ),
) -> None:
    """Run TODAY's slice incrementally — stop as soon as we hit known-recent cars.

    The EncAr list API returns cars in ModifiedDate order (newest first).
    The incremental walker fetches one page at a time and stops when the
    newest item on the page was already seen within ``--cooldown-hours``
    (default 12h). Most daily runs process 0-200 cars per model and
    finish in well under an hour.

    Respects per-model cooldown: a model that ran recently is skipped
    even if it is today's bucket. After processing, dedup runs at the
    end so freshly-added listings collapse into the existing groups.
    """
    setup_logging()
    asyncio.run(_run_incremental_async(
        cooldown_hours=cooldown_hours,
        bucket_count=bucket_count,
        max_models=max_models,
        max_pages=max_pages,
    ))


async def _run_incremental_async(
    *,
    cooldown_hours: int,
    bucket_count: int,
    max_models: int,
    max_pages: int,
) -> None:
    settings = get_settings()
    Session = get_sessionmaker()
    cooldown = cooldown_hours or settings.scheduler_cooldown_hours
    buckets = bucket_count or settings.scheduler_bucket_count
    pages = max_pages or settings.max_pages
    today = datetime.now(UTC).date()

    mem = MemSampler(interval_sec=60.0, label="incremental")
    mem.start()
    try:
        async with Session() as session:
            enabled = await get_enabled_models(session)
            plan = plan_today(enabled, today, bucket_count=buckets, cooldown_hours=cooldown)
            log.info(
                "incremental_plan",
                **plan.as_dict(),
            )
            today_models = plan.today_models
            if max_models and len(today_models) > max_models:
                log.info(
                    "max_models_cap",
                    before=len(today_models),
                    after=max_models,
                )
                today_models = today_models[:max_models]
            if not today_models:
                typer.echo("No models scheduled for today (all deferred or empty bucket).")
                return

            total_cars = 0
            suppressed_errors = 0

            async with ApiFetcher() as api:
                browser: BrowserFetcher | None = None
                try:
                    browser = BrowserFetcher()
                    await browser.__aenter__()
                except Exception as e:
                    log.warning(
                        "browser_fetcher_unavailable",
                        error=str(e),
                        hint="run `playwright install chromium --with-deps` to enable fallback",
                    )
                    browser = None

                if browser is not None:
                    fetcher: Fetcher = FallbackFetcher(primary=api, secondary=browser)
                else:
                    fetcher = api  # type: ignore[assignment]
                request_delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)

                try:
                    for sm in today_models:
                        try:
                            n = await run_model_incremental(
                                sm,
                                fetcher=fetcher,
                                session=session,
                                list_url_for_page=lambda page, action=sm.encar_action: make_list_url_for_page(action, page),
                                detail_url_template=settings.api_detail_template,
                                request_delay=request_delay,
                                max_pages=pages,
                                cooldown_hours=cooldown,
                            )
                            total_cars += n
                        except Exception as e:
                            suppressed_errors += 1
                            log.error("model_failed", slug=sm.slug, error=str(e))
                        session.expunge_all()
                        await asyncio.sleep(random.uniform(
                            settings.min_model_delay_sec,
                            settings.max_model_delay_sec,
                        ))
                finally:
                    if browser is not None:
                        await browser.__aexit__(None, None, None)

            # Phase 2: dedup at the end so newcomers fold into existing groups.
            dedup_report = await run_dedup(session)
            await session.commit()
            log.info(
                "dedup_done",
                duplicate_groups=dedup_report.duplicate_groups,
                rows_hidden=dedup_report.rows_hidden,
                rows_primary=dedup_report.rows_primary,
            )

            summary = mem.stop()
            typer.echo(json.dumps({
                "mode": "incremental",
                "today": today.isoformat(),
                "bucket": f"{plan.bucket_index}/{plan.bucket_count}",
                "models_run": len(today_models),
                "models_deferred_cooldown": plan.skipped_due_to_cooldown,
                "cars_fetched": total_cars,
                "suppressed_errors": suppressed_errors,
                "dedup": dedup_report.as_dict(),
                "memory": summary.as_dict(),
            }, ensure_ascii=False, indent=2))
    finally:
        if mem._thread is not None and mem._thread.is_alive():
            mem.stop()


@app.command()
def backfill(
    resume: bool = typer.Option(True, "--resume/--no-resume",
        help="Resume from the saved state file (default: resume)."),
    reset: bool = typer.Option(False, "--reset",
        help="Delete the state file and start fresh. Destructive!"),
    chunk_size: int = typer.Option(0, "--chunk-size",
        help="Models per resume unit (informational; 0 = unset)."),
    max_pages: int = typer.Option(0, "--max-pages",
        help="Override settings.max_pages for this run."),
    max_models: int = typer.Option(0, "--max-models",
        help="Cap the run to this many models (0 = all)."),
    yes: bool = typer.Option(False, "--yes", "-y",
        help="Skip the 'are you sure' confirmation."),
) -> None:
    """Walk EVERY enabled model once. Resumable: a crash mid-run picks up
    at the last completed slug on the next invocation.

    The state file (``/var/log/backfill_state.json``) is written after
    every model. Delete it (``--reset``) only if you want to start
    from zero.

    NOT scheduled by cron — backfill is a manual operation. Run it
    once after deploy to populate the catalog, then rely on
    ``run-incremental`` for daily refresh.
    """
    setup_logging()
    if reset and not yes:
        typer.confirm(
            "This will DELETE /var/log/backfill_state.json and restart the backfill. Continue?",
            abort=True,
        )
    asyncio.run(_backfill_async(
        resume=resume, reset=reset, chunk_size=chunk_size,
        max_pages=max_pages, max_models=max_models,
    ))


async def _backfill_async(
    *, resume: bool, reset: bool, chunk_size: int,
    max_pages: int, max_models: int,
) -> None:
    settings = get_settings()
    state_path = Path(settings.backfill_state_path)
    if reset:
        from encar_parser.backfill import reset_state
        reset_state(state_path)
        log.info("backfill_state_reset", path=str(state_path))

    Session = get_sessionmaker()
    mem = MemSampler(interval_sec=60.0, label="backfill")
    mem.start()
    pages = max_pages or settings.max_pages

    try:
        async with Session() as session:
            enabled = await get_enabled_models(session)

            # Track which slugs are completed across runs by reading from
            # the state file (the CLI side filters via filter_remaining).
            from encar_parser.backfill import filter_remaining, load_state
            state = load_state(state_path) if resume else None
            remaining = filter_remaining(enabled, state)
            if max_models and len(remaining) > max_models:
                log.info("max_models_cap", before=len(remaining), after=max_models)
                remaining = remaining[:max_models]

            async with ApiFetcher() as api:
                browser: BrowserFetcher | None = None
                try:
                    browser = BrowserFetcher()
                    await browser.__aenter__()
                except Exception as e:
                    log.warning("browser_fetcher_unavailable", error=str(e))
                    browser = None

                if browser is not None:
                    fetcher: Fetcher = FallbackFetcher(primary=api, secondary=browser)
                else:
                    fetcher = api  # type: ignore[assignment]
                request_delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)

                def on_state_change(s):
                    log.info(
                        "backfill_progress",
                        completed=len(s.completed_slugs),
                        current=s.current_slug,
                        total=s.models_total,
                    )

                async def run_one(sm):
                    return await run_model(
                        sm,
                        fetcher=fetcher,
                        session=session,
                        list_url_for_page=lambda page, action=sm.encar_action: make_list_url_for_page(action, page),
                        detail_url_template=settings.api_detail_template,
                        request_delay=request_delay,
                        max_pages=pages,
                    )

                try:
                    summary = await walk_backfill(
                        remaining,
                        state_path,
                        run_one=run_one,
                        chunk_size=chunk_size,
                        on_state_change=on_state_change,
                    )
                finally:
                    if browser is not None:
                        await browser.__aexit__(None, None, None)

            # Dedup at the very end so the freshly-backfilled catalog
            # collapses into one row per physical car.
            dedup_report = await run_dedup(session)
            await session.commit()
            log.info(
                "dedup_done",
                duplicate_groups=dedup_report.duplicate_groups,
                rows_hidden=dedup_report.rows_hidden,
                rows_primary=dedup_report.rows_primary,
            )

            sample = mem.stop()
            typer.echo(json.dumps({
                "mode": "backfill",
                **summary,
                "dedup": dedup_report.as_dict(),
                "memory": sample.as_dict(),
            }, ensure_ascii=False, indent=2))
    finally:
        if mem._thread is not None and mem._thread.is_alive():
            mem.stop()


@app.command()
def plan(
    day: str = typer.Option(
        "", "--day",
        help="ISO date to plan (e.g. 2026-06-21). Empty = today.",
    ),
    days: int = typer.Option(
        1, "--days", "-n",
        help="Plan N consecutive days (default 1 = today only).",
    ),
    bucket_count: int = typer.Option(
        0, "--bucket-count",
        help="Override settings.scheduler_bucket_count.",
    ),
    cooldown_hours: int = typer.Option(
        0, "--cooldown-hours",
        help="Override settings.scheduler_cooldown_hours.",
    ),
    per_car_sec: float = typer.Option(
        0.0, "--per-car-sec",
        help="Per-car fetch time estimate in seconds. 0 = use default (4.0).",
    ),
    probe: bool = typer.Option(
        False, "--probe",
        help="Hit the live EncAr API for fresh per-model counts (slow).",
    ),
    cache_path: str = typer.Option(
        "", "--cache",
        help="Override settings.plan_counts_cache.",
    ),
    json_output: bool = typer.Option(False, "--json",
        help="Emit JSON instead of human text."),
) -> None:
    """Dry-run: show what would be parsed, with time estimates. No network
    unless ``--probe`` is passed.

    By default the planner uses cached per-model Counts from
    ``/var/log/encar_counts.json``. Write that cache once with
    ``plan --probe``, then every subsequent dry-run is instant.

    Examples
    --------

        python -m encar_parser plan                    # today, human-readable
        python -m encar_parser plan --days 14          # full 14-day rotation
        python -m encar_parser plan --json             # machine-readable
        python -m encar_parser plan --probe            # refresh counts cache
        python -m encar_parser plan --day 2026-06-21   # specific date
    """
    setup_logging()
    asyncio.run(_plan_async(
        day=day or None, days=days, bucket_count=bucket_count,
        cooldown_hours=cooldown_hours, per_car_sec=per_car_sec,
        probe=probe, cache_path=cache_path or None,
        json_output=json_output,
    ))


async def _plan_async(
    *, day: str | None, days: int, bucket_count: int, cooldown_hours: int,
    per_car_sec: float, probe: bool, cache_path: str | None, json_output: bool,
) -> None:
    settings = get_settings()
    from datetime import date as _date
    target_day = _date.fromisoformat(day) if day else None
    cache = Path(cache_path or settings.plan_counts_cache)
    Session = get_sessionmaker()
    async with Session() as session:
        enabled = await get_enabled_models(session)
    rotation = await run_plan_cli(
        enabled_models=enabled,
        day=target_day,
        days=days,
        bucket_count=bucket_count or settings.scheduler_bucket_count,
        cooldown_hours=cooldown_hours or settings.scheduler_cooldown_hours,
        per_car_sec=per_car_sec or 4.0,
        avg_count=469,
        counts_cache=cache,
        probe=probe,
    )
    if json_output:
        typer.echo(json.dumps(rotation.as_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(render_rotation_text(rotation))


@app.command()
def probe(
    slug: str = typer.Argument(..., help="slug from models.yaml to test"),
    config_path: Path = typer.Option(Path("models.yaml"), "--config", "-c"),
    detail_id: int = typer.Option(
        0, "--detail-id", help="also fetch this car-detail id from the API"
    ),
) -> None:
    """Hit the live encar API for one model and print raw JSON.

    Use this to confirm the real field names before trusting the parsers.
    """
    setup_logging()
    asyncio.run(_probe_async(slug, config_path, detail_id))


async def _probe_async(slug: str, config_path: Path, detail_id: int) -> None:
    from encar_parser.encar_url import ModelConfig, build_list_api_url
    from encar_parser.parsers.list_page import parse_search_list_result

    settings = get_settings()
    items = _load_models_yaml(config_path)
    match = next((i for i in items if i.get("slug") == slug), None)
    if match is None:
        typer.echo(f"slug '{slug}' not found in {config_path}", err=True)
        raise typer.Exit(1)

    cfg = ModelConfig(**{k: v for k, v in match.items() if k != "enabled"})
    list_url = build_list_api_url(cfg)
    typer.echo(f"LIST URL:\n{list_url}\n")

    async with ApiFetcher() as api:
        resp = await api.get(list_url, referer=settings.encar_referer)
        try:
            payload = resp.json()
        except Exception:
            typer.echo("Response was not JSON. First 500 bytes:", err=True)
            typer.echo(resp.text()[:500])
            raise typer.Exit(2)

        parsed = parse_search_list_result(payload)
        typer.echo(f"Count={parsed.total}  parsed_items={len(parsed.items)}")
        if parsed.items:
            typer.echo("First item: " + json.dumps(
                parsed.items[0].__dict__, ensure_ascii=False
            ))
        # Show the top-level keys so you can map fields if the shape differs.
        if isinstance(payload, dict):
            typer.echo("Top-level keys: " + ", ".join(map(str, payload.keys())))

        if detail_id:
            durl = settings.api_detail_template.format(encar_id=detail_id)
            typer.echo(f"\nDETAIL URL:\n{durl}")
            dresp = await api.get(durl, referer=settings.encar_referer)
            typer.echo(dresp.text()[:1500])


# ── Phase 5: brand → CarType audit ────────────────────────────────────────


@app.command("classify-brands")
def classify_brands(
    config_path: Path = typer.Option(Path("models.yaml"), "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
) -> None:
    """Audit every brand in models.yaml against the CarType classification.

    Reads each model's manufacturer, looks it up in the domestic/import
    maps in :mod:`encar_parser.car_type`, and reports the result. Use this
    to spot:

    * brands that defaulted to "N" because the classifier didn't know
      them — add to DOMESTIC_BRANDS_EN_TO_KR or KNOWN_IMPORT_BRANDS
    * model-level ``car_type_code:`` values that disagree with the
      classifier's view (a likely typo in models.yaml)
    * brands that no longer exist in the catalog (no entries at all)

    Run this after every change to models.yaml or to the classification
    maps in encar_parser/car_type.py.
    """
    setup_logging()
    if not config_path.exists():
        typer.echo(f"models file not found: {config_path}", err=True)
        raise typer.Exit(1)
    items = yaml.safe_load(config_path.read_text(encoding="utf-8")).get("models", [])

    rows: list[dict[str, Any]] = []
    for item in items:
        mfr = item.get("manufacturer")
        explicit = item.get("car_type_code")
        derived, was_domestic = classify_brand(mfr)
        rows.append({
            "slug": item.get("slug"),
            "manufacturer": mfr,
            "car_type_code_yaml": explicit,
            "car_type_code_derived": derived,
            "was_domestic": was_domestic,
            "known_brand": is_known_brand(mfr),
            "agrees": explicit is None or explicit == derived,
        })

    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    # Human-readable layout.
    by_code: dict[str, list[dict[str, Any]]] = {
        CAR_TYPE_DOMESTIC: [], CAR_TYPE_IMPORT: [], "?": [],
    }
    for r in rows:
        code = r["car_type_code_yaml"] or "?"
        by_code.setdefault(code, []).append(r)

    typer.echo("=== models.yaml CarType classification audit ===")
    typer.echo(f"models total            : {len(rows)}")
    typer.echo(f"  domestic (Y in YAML)  : {len(by_code[CAR_TYPE_DOMESTIC])}")
    typer.echo(f"  import   (N in YAML)  : {len(by_code[CAR_TYPE_IMPORT])}")
    typer.echo(f"  no car_type_code set  : {len(by_code['?'])}")
    typer.echo()

    # Disagreements between explicit YAML value and the classifier's view.
    disagreements = [r for r in rows if r["car_type_code_yaml"] is not None and not r["agrees"]]
    if disagreements:
        typer.echo(f"--- disagreements ({len(disagreements)}) ---")
        for r in disagreements:
            typer.echo(
                f"  {r['slug']:<24}  mfr={r['manufacturer']!r:<14}  "
                f"yaml={r['car_type_code_yaml']}  derived={r['car_type_code_derived']}"
            )
        typer.echo()

    # Brands the classifier didn't know — defaulted to "N". These are the
    # ones to either add to KNOWN_IMPORT_BRANDS or DOMESTIC_BRANDS_EN_TO_KR.
    unknown = [r for r in rows if not r["known_brand"] and r["manufacturer"]]
    if unknown:
        typer.echo(f"--- unknown brands (defaulted to {CAR_TYPE_IMPORT}; ADD to classification map) ---")
        # Group by manufacturer to keep the report compact.
        by_mfr: dict[str, int] = {}
        for r in unknown:
            by_mfr[r["manufacturer"]] = by_mfr.get(r["manufacturer"], 0) + 1
        for mfr, n in sorted(by_mfr.items()):
            typer.echo(f"  {mfr!r:<20} ({n} model{'s' if n != 1 else ''})")
        typer.echo()

    # Per-manufacturer breakdown with explicit counts.
    by_mfr: dict[str, dict[str, int]] = {}
    for r in rows:
        m = r["manufacturer"] or "<missing>"
        by_mfr.setdefault(m, {"Y": 0, "N": 0, "total": 0})
        code = r["car_type_code_yaml"] or "?"
        if code in ("Y", "N"):
            by_mfr[m][code] += 1
        by_mfr[m]["total"] += 1
        by_mfr[m]["known"] = int(r["known_brand"])

    typer.echo("--- by manufacturer ---")
    typer.echo(f"  {'manufacturer':<20} {'total':>5} {'Y':>4} {'N':>4}  known")
    for mfr in sorted(by_mfr.keys(), key=lambda x: (-by_mfr[x]["total"], x)):
        s = by_mfr[mfr]
        typer.echo(
            f"  {mfr:<20} {s['total']:>5} {s['Y']:>4} {s['N']:>4}  "
            f"{'yes' if s.get('known') else 'NO (default)'}"
        )
