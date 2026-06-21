"""FastAPI web viewer for the encar parser.

Two endpoints:

* ``GET /`` — renders an HTML table of cars currently in the DB.
* ``GET /img?src=<urlencoded_url>`` — proxy that fetches an image from
  the working encar photo CDN (``ci.encar.com``) and streams the bytes
  back. See :mod:`encar_parser.web.img_proxy` for the security model.

Deployment
──────────
Run via uvicorn from the project root::

    .venv/bin/python -m uvicorn encar_parser.web.app:app --host 0.0.0.0 --port 8090

In docker-compose this happens in the ``web`` service (see
``docker-compose.yml``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from encar_parser.config import get_settings
from encar_parser.db.models import Car
from encar_parser.db.session import get_sessionmaker as _prod_sessionmaker
from encar_parser.photos import first_photo_proxy_src
from encar_parser.translations import translate_color
from encar_parser.web.img_proxy import ProxyError, fetch_image

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_jinja() -> Environment:
    """Jinja2 env that autoescapes HTML by default — user-controlled data
    (car fields, photo URLs) flows into the table, so escaping is critical."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def create_app(sessionmaker: async_sessionmaker[AsyncSession] | None = None) -> FastAPI:
    """Build the FastAPI app.

    `sessionmaker` is overridable for tests (pass an aiosqlite sessionmaker
    instead of the production postgres one).
    """
    sm = sessionmaker or _prod_sessionmaker()
    app = FastAPI(title="encar-parser web viewer", docs_url=None, redoc_url=None)
    jinja = _build_jinja()
    template = jinja.get_template("index.html")
    settings = get_settings()

    # ── helpers ─────────────────────────────────────────────────────────

    def _price_rub(krw: int | None) -> int | None:
        if krw is None:
            return None
        return int(round(krw * settings.krw_to_rub_rate))

    async def _load_cars(s: AsyncSession) -> tuple[list[dict[str, Any]], datetime | None]:
        """Pull the most-recent cars and the latest ``last_seen_at``.

        Filters to ``is_primary = True`` only — duplicate listings of the
        same physical car are hidden from the vitrine. The counter
        (``len(rows)``) therefore reflects unique cars, not raw rows.
        See :mod:`encar_parser.dedup` for the grouping logic.

        Note on colors: we re-translate from ``color_original`` at render time
        rather than trusting the stored ``color_ru``. The DB column was
        populated with whatever the parser's translation dict knew at the
        time the row was inserted — so cars parsed before e.g. ``청색`` was
        added carry the raw Korean string in ``color_ru``. Re-translating
        here means the web view always reflects the *current* dict without
        needing a data migration.
        """
        cars = (await s.scalars(
            select(Car)
            .where(Car.is_primary.is_(True))
            .order_by(Car.last_seen_at.desc().nullslast(), Car.encar_id.desc())
            .limit(500)
        )).all()
        # ``last_seen`` reflects the freshness of the DB itself (any new
        # listing, primary or hidden duplicate) — it answers "when was the
        # last run?", not "when was the latest visible row added?".
        last_seen = await s.scalar(select(func.max(Car.last_seen_at)))
        rows: list[dict[str, Any]] = []
        for c in cars:
            rows.append({
                "encar_id": c.encar_id,
                "brand": c.brand,
                "model": c.model,
                "year_month": c.year_month,
                "mileage_km": c.mileage_km,
                "price_krw": c.price_krw,
                "price_rub": _price_rub(c.price_krw),
                "fuel_ru": c.fuel_ru,
                "transmission_ru": c.transmission_ru,
                # Always translate at render time, falling back to the stored
                # value if color_original is missing.
                "color_ru": (translate_color(c.color_original) if c.color_original else c.color_ru),
                "encar_detail_url": c.encar_detail_url,
                "thumb_src": first_photo_proxy_src(c.photo_urls),
            })
        return rows, last_seen

    # ── routes ──────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        async with sm() as s:
            rows, last_seen = await _load_cars(s)
        html = template.render(
            cars=rows,
            car_count=len(rows),
            krw_to_rub_rate=settings.krw_to_rub_rate,
            last_seen=last_seen,
            now=datetime.now(UTC),
        )
        return HTMLResponse(html)

    @app.get("/img")
    async def img(src: str = Query(...)) -> Response:
        try:
            data, content_type = await fetch_image(src)
        except ProxyError as e:
            # Anything from allowlist violations to upstream 4xx becomes
            # 404 — never reveal that this is a forwarder.
            raise HTTPException(status_code=404, detail=str(e)) from e
        return Response(content=data, media_type=content_type, headers={
            "Cache-Control": "public, max-age=3600",
        })

    return app


# Module-level app for uvicorn: `uvicorn encar_parser.web.app:app`.
# Built lazily on first import so config / DB don't crash at module load.
app = create_app()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Public re-export so tests can monkey-patch it (used in
    tests/integration/test_web_app.py to inject aiosqlite)."""
    return _prod_sessionmaker()
