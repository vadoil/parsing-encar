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
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.browser import BrowserFetcher
from encar_parser.fetchers.factory import FallbackFetcher
from encar_parser.pipeline import make_list_url_for_page, run_model
from encar_parser.scheduler import models_for_today
from encar_parser.utils.log import get_logger, setup_logging
from encar_parser.utils.rate_limit import RandomDelay

log = get_logger(__name__)
app = typer.Typer(help="Encar parser CLI")


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
def run() -> None:
    """Run today's scheduled models."""
    setup_logging()
    asyncio.run(_run_async())


async def _run_async() -> None:
    settings = get_settings()
    Session = get_sessionmaker()
    async with Session() as session:
        all_models = await get_enabled_models(session)
        today_models = models_for_today(all_models, datetime.now(UTC).date())
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

        async with ApiFetcher() as api, BrowserFetcher() as browser:
            fetcher = FallbackFetcher(primary=api, secondary=browser)
            request_delay = RandomDelay(settings.min_delay_sec, settings.max_delay_sec)
            for sm in today_models:
                try:
                    count = await run_model(
                        sm,
                        fetcher=fetcher,
                        session=session,
                        list_url_for_page=lambda page, action=sm.encar_action: make_list_url_for_page(action, page),  # noqa: E501
                        detail_url_template=settings.api_detail_template,
                        request_delay=request_delay,
                        max_pages=settings.max_pages,
                    )
                    run_record.models_done += 1
                    run_record.cars_fetched += count
                except Exception as e:
                    run_record.cars_failed += 1
                    log.error("model_failed", slug=sm.slug, error=str(e))
                    if run_record.error_log is None:
                        run_record.error_log = []
                    run_record.error_log.append({"slug": sm.slug, "error": str(e)})
                # Pause between models
                await asyncio.sleep(random.uniform(
                    settings.min_model_delay_sec, settings.max_model_delay_sec
                ))

        run_record.finished_at = datetime.now(UTC)
        await session.commit()

        typer.echo(json.dumps({
            "run_id": run_record.id,
            "models_planned": run_record.models_planned,
            "models_done": run_record.models_done,
            "cars_fetched": run_record.cars_fetched,
            "cars_failed": run_record.cars_failed,
        }, ensure_ascii=False, indent=2))


@app.command()
def migrate() -> None:
    """Run alembic migrations."""
    import subprocess
    setup_logging()
    result = subprocess.run(["alembic", "upgrade", "head"], check=False)
    raise typer.Exit(result.returncode)


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
