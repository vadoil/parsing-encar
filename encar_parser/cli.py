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
from encar_parser.pipeline import make_list_url_for_page, run_model
from encar_parser.scheduler import models_for_today
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
