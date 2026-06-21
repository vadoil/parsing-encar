"""FastAPI web viewer + CRM for the encar parser.

Five server-rendered pages (Jinja2 + a tiny bit of hand-written CSS,
no external CDN):

* ``GET /``            — Машины: vitrine of primary listings (cars).
* ``GET /categories``  — Категории: every model in ``search_models``
                         with car counts and a "open on Encar" link.
* ``GET /parsing``     — Парсинг: dashboard (totals + last 10 runs +
                         per-model last-run-at).
* ``GET /history``     — История: full runs table, newest first,
                         capped to ~100.
* ``GET /settings``    — Настройки: read-only view of current
                         ``Settings`` from .env / defaults.

Plus the image proxy:

* ``GET /img?src=…``   — fetches photos from the Encar photo CDN
                         (ci.encar.com) and streams the bytes back.
                         See :mod:`encar_parser.web.img_proxy` for the
                         security model.

Bind is always 127.0.0.1 — the CRM is NOT exposed to the internet.
SSH tunnel only. See PROJECT_REPORT.md §12 for the deploy recipe.
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
from encar_parser.db.models import Car, CarModelMatch, Run, SearchModel
from encar_parser.db.session import get_sessionmaker as _prod_sessionmaker
from encar_parser.photos import first_photo_proxy_src
from encar_parser.translations import translate_color
from encar_parser.web.img_proxy import ProxyError, fetch_image
from encar_parser.web.links import encar_web_url, extract_car_type_from_action

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
    app = FastAPI(title="encar-parser CRM", docs_url=None, redoc_url=None)
    jinja = _build_jinja()
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
        """
        cars = (await s.scalars(
            select(Car)
            .where(Car.is_primary.is_(True))
            .order_by(Car.last_seen_at.desc().nullslast(), Car.encar_id.desc())
            .limit(500)
        )).all()
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

    async def _load_categories(s: AsyncSession) -> list[dict[str, Any]]:
        """Every model in ``search_models`` with primary-car counts.

        One round-trip with a LEFT JOIN through the car_model_matches
        junction table. Avoids the N+1 of per-model count queries.
        """
        rows = await s.execute(
            select(
                SearchModel.id,
                func.count(func.distinct(Car.encar_id)).label("n"),
            )
            .select_from(SearchModel)
            .outerjoin(
                CarModelMatch,
                CarModelMatch.search_model_id == SearchModel.id,
            )
            .outerjoin(
                Car,
                (Car.encar_id == CarModelMatch.encar_id)
                & (Car.is_primary.is_(True)),
            )
            .group_by(SearchModel.id)
        )
        car_counts: dict[int, int] = {sid: int(n) for sid, n in rows.all()}

        models = (await s.scalars(
            select(SearchModel).order_by(
                SearchModel.enabled.desc().nullslast(),
                SearchModel.priority, SearchModel.slug,
            )
        )).all()
        out: list[dict[str, Any]] = []
        for m in models:
            action = (m.encar_action or {}).get("q", "")
            out.append({
                "id": m.id,
                "slug": m.slug,
                "name": m.name,
                "manufacturer": (m.encar_action or {}).get("manufacturer"),
                "car_type_code": extract_car_type_from_action(action),
                "enabled": m.enabled,
                "priority": m.priority,
                "cars_count": car_counts.get(m.id, 0),
                "last_run_at": m.last_run_at,
                "web_url": encar_web_url(m),
            })
        return out

    async def _load_parsing(s: AsyncSession) -> dict[str, Any]:
        """Aggregated state for the parsing dashboard."""
        per_model_rows = await s.execute(
            select(
                SearchModel.id,
                SearchModel.slug,
                SearchModel.priority,
                SearchModel.enabled,
                SearchModel.last_run_at,
                func.count(func.distinct(Car.encar_id)).label("n"),
            )
            .select_from(SearchModel)
            .outerjoin(
                CarModelMatch,
                CarModelMatch.search_model_id == SearchModel.id,
            )
            .outerjoin(
                Car,
                (Car.encar_id == CarModelMatch.encar_id)
                & (Car.is_primary.is_(True)),
            )
            .where(SearchModel.enabled.is_(True))
            .group_by(SearchModel.id)
            .order_by(SearchModel.priority, SearchModel.slug)
        )
        per_model = [
            {
                "slug": r.slug,
                "priority": r.priority,
                "enabled": r.enabled,
                "last_run_at": r.last_run_at,
                "cars_count": int(r.n),
            }
            for r in per_model_rows.all()
        ]

        total_models = await s.scalar(select(func.count()).select_from(SearchModel))
        enabled_models = await s.scalar(
            select(func.count()).select_from(SearchModel).where(SearchModel.enabled.is_(True))
        )
        disabled_models = (total_models or 0) - (enabled_models or 0)
        cars_total = await s.scalar(select(func.count()).select_from(Car))
        cars_primary = await s.scalar(
            select(func.count()).select_from(Car).where(Car.is_primary.is_(True))
        )
        runs_total = await s.scalar(select(func.count()).select_from(Run))
        recent_runs = (await s.scalars(
            select(Run).order_by(Run.started_at.desc().nullslast()).limit(10)
        )).all()

        return {
            "total_models": total_models or 0,
            "enabled_models": enabled_models or 0,
            "disabled_models": disabled_models,
            "cars_total": cars_total or 0,
            "cars_primary": cars_primary or 0,
            "runs_total": runs_total or 0,
            "recent_runs": recent_runs,
            "per_model": per_model,
        }

    async def _load_history(s: AsyncSession, limit: int = 100) -> list[Run]:
        return list((await s.scalars(
            select(Run).order_by(Run.started_at.desc().nullslast()).limit(limit)
        )).all())

    def _render(request: Request, template_name: str, **ctx: Any) -> HTMLResponse:
        template = jinja.get_template(template_name)
        return HTMLResponse(template.render(request=request, **ctx))

    # ── routes ──────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def cars(request: Request) -> HTMLResponse:
        async with sm() as s:
            rows, last_seen = await _load_cars(s)
        return _render(
            request, "cars.html",
            cars=rows,
            car_count=len(rows),
            krw_to_rub_rate=settings.krw_to_rub_rate,
            last_seen=last_seen,
            now=datetime.now(UTC),
        )

    @app.get("/categories", response_class=HTMLResponse)
    async def categories(request: Request) -> HTMLResponse:
        async with sm() as s:
            models = await _load_categories(s)
        return _render(
            request, "categories.html",
            models=models,
            total_models=len(models),
            enabled_models=sum(1 for m in models if m["enabled"]),
            with_cars=sum(1 for m in models if m["cars_count"] > 0),
        )

    @app.get("/parsing", response_class=HTMLResponse)
    async def parsing(request: Request) -> HTMLResponse:
        async with sm() as s:
            data = await _load_parsing(s)
        return _render(request, "parsing.html", **data)

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request) -> HTMLResponse:
        async with sm() as s:
            runs = await _load_history(s)
        return _render(request, "history.html", runs=runs)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        return _render(request, "settings.html", settings=settings)

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
