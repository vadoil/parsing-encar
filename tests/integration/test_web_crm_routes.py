"""Integration tests for the four new CRM routes: /categories, /parsing,
/history, /settings.

Each test seeds an in-memory aiosqlite DB via the existing
``Base.metadata.create_all`` pattern (same shape as the /  tests in
``test_web_app.py``) and verifies that the route returns 200, contains
the expected content, and respects the navigation active state.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, Run, SearchModel
from encar_parser.web.app import create_app


@pytest.fixture
async def session():
    """In-memory aiosqlite with all tables (cars, models, runs, matches)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    yield Session, engine
    await engine.dispose()


@pytest.fixture
async def client(session):
    Session, _ = session
    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── /categories ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_categories_lists_models_with_counts_and_links(client, session):
    """Every model in search_models appears, with primary-car count and a
    link to Encar. CarType badge (Y/N) is derived from the q string."""
    Session, _ = session
    async with Session() as s:
        # Model with 2 primary cars + 1 hidden duplicate (count = 2)
        m1 = SearchModel(
            slug="bmw-x6-g06", name="BMW X6 (G06)",
            encar_url="", priority=100,
            encar_action={
                "q": "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.(C.ModelGroup.X6._.Model.X6 (G06).)))_.Year.range(201800..202699).)",
                "sort": "ModifiedDate", "limit": 20,
            },
            enabled=True,
        )
        s.add(m1)
        await s.flush()
        for cid in (100, 101):
            s.add(Car(
                encar_id=cid, brand="BMW", model="X6 (G06)",
                year_month=date(2024, 1, 1), mileage_km=10000,
                color_original="흰색", price_krw=50_000_000,
                photo_urls=[], last_seen_at=datetime.now(UTC),
                is_primary=True,
            ))
            await s.flush()
            s.add(CarModelMatch(search_model_id=m1.id, encar_id=cid,
                                first_matched_at=datetime.now(UTC),
                                last_matched_at=datetime.now(UTC)))
        # Hidden duplicate — must NOT count
        s.add(Car(
            encar_id=999, brand="BMW", model="X6 (G06)",
            year_month=date(2024, 1, 1), mileage_km=10000,
            color_original="흰색", price_krw=50_000_000,
            photo_urls=[], last_seen_at=datetime.now(UTC),
            is_primary=False,
        ))
        # Model with 0 cars (enabled but never run)
        s.add(SearchModel(
            slug="genesis-g80", name="Genesis G80 (RG3)",
            encar_url="", priority=50,
            encar_action={
                "q": "(And.Hidden.N._.(C.CarType.Y._.(C.Manufacturer.제네시스._.(C.ModelGroup.G80._.Model.G80 (RG3).)))_.Year.range(201800..202699).)",
                "sort": "ModifiedDate", "limit": 20,
            },
            enabled=True,
        ))
        await s.commit()

    r = await client.get("/categories")
    assert r.status_code == 200
    body = r.text
    # Header counts
    assert "Всего моделей" in body
    assert "enabled:" in body
    # Rows
    assert "bmw-x6-g06" in body
    assert "genesis-g80" in body
    # CarType badges
    assert ">N</span>" in body or ">N<" in body  # BMW = import
    assert ">Y</span>" in body or ">Y<" in body  # Genesis = domestic
    # Count for BMW (2) appears; count for Genesis (0) appears
    assert ">2</td>" in body or "> 2 </td>" in body
    assert "никогда" in body  # Genesis never run
    # Open-on-Encar link — should hit the front-end URL with hash
    assert "www.encar.com" in body
    assert "carType=for" in body  # BMW
    assert "carType=kor" in body  # Genesis
    assert "#!" in body  # hash payload present


@pytest.mark.asyncio
async def test_categories_empty_state_shows_helpful_message(client):
    r = await client.get("/categories")
    assert r.status_code == 200
    assert "В БД пока нет моделей" in r.text
    assert "python -m encar_parser sync" in r.text


@pytest.mark.asyncio
async def test_categories_highlights_active_nav_link(client):
    r = await client.get("/categories")
    assert r.status_code == 200
    body = r.text
    # The nav link for /categories must have class="active"
    assert '<a href="/categories" class="active"' in body


# ── /parsing ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parsing_dashboard_shows_totals_and_recent_runs(client, session):
    Session, _ = session
    async with Session() as s:
        for i in range(3):
            s.add(SearchModel(
                slug=f"m{i}", name=f"M{i}", encar_url="",
                encar_action={"q": "(C.CarType.N._.)"},
                enabled=(i < 2), priority=10 * (i + 1),
            ))
        s.add(Run(
            started_at=datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 21, 13, 30, tzinfo=UTC),
            models_planned=2, models_done=2, cars_fetched=150,
            cars_failed=3, error_log=[{"slug": "m1", "error": "timeout"}],
        ))
        s.add(Run(
            started_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 20, 13, 0, tzinfo=UTC),
            models_planned=2, models_done=2, cars_fetched=80,
            cars_failed=0, error_log=[],
        ))
        await s.commit()

    r = await client.get("/parsing")
    assert r.status_code == 200
    body = r.text
    # Totals
    assert ">3<" in body  # total_models
    assert ">2<" in body  # enabled_models (2 enabled + 1 disabled)
    assert ">1<" in body  # disabled_models
    # Recent runs table
    assert "2026-06-21 12:00" in body
    assert "2026-06-20 12:00" in body
    assert "150" in body  # cars_fetched of the first run
    assert ">1</td>" in body  # errors (cap) count for the first run


@pytest.mark.asyncio
async def test_parsing_empty_state(client):
    r = await client.get("/parsing")
    assert r.status_code == 200
    assert "Прогонов ещё не было" in r.text


@pytest.mark.asyncio
async def test_parsing_active_nav_link(client):
    r = await client.get("/parsing")
    assert '<a href="/parsing" class="active"' in r.text


# ── /history ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_table_orders_newest_first_and_shows_duration(client, session):
    Session, _ = session
    async with Session() as s:
        s.add(Run(
            started_at=datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 21, 12, 5, tzinfo=UTC),  # 5 minutes
            models_planned=2, models_done=2, cars_fetched=42,
            cars_failed=0, error_log=[],
        ))
        s.add(Run(
            started_at=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 14, 14, 30, tzinfo=UTC),  # 2.5h
            models_planned=2, models_done=2, cars_fetched=800,
            cars_failed=10,
            error_log=[
                {"slug": "m1", "error": "timeout"},
                {"slug": "m2", "error": "http 403"},
            ],
        ))
        await s.commit()

    r = await client.get("/history")
    assert r.status_code == 200
    body = r.text
    # Newest first
    assert body.index("2026-06-21") < body.index("2026-06-14")
    # Duration formatting — 5m and 2.5ч
    assert "5м" in body or "5.0м" in body
    assert "ч" in body  # 2.5ч
    # Error rows surfaced inline
    assert "timeout" in body
    assert "http 403" in body


@pytest.mark.asyncio
async def test_history_active_nav_link(client):
    r = await client.get("/history")
    assert '<a href="/history" class="active"' in r.text


# ── /settings ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_shows_readonly_with_kv_pairs(client):
    r = await client.get("/settings")
    assert r.status_code == 200
    body = r.text
    # Every key settings field appears at least once
    assert "krw_to_rub_rate" in body
    assert "web_port" in body
    assert "scheduler_bucket_count" in body
    assert "scheduler_cooldown_hours" in body
    assert "img_proxy_allowed_hosts" in body
    # Read-only marker is prominent
    assert "Read-only" in body or "read-only" in body
    assert "Редактирование" in body or "следующим шагом" in body


@pytest.mark.asyncio
async def test_settings_active_nav_link(client):
    r = await client.get("/settings")
    assert '<a href="/settings" class="active"' in r.text


# ── all five routes smoke test ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_five_routes_return_200(client):
    """Each page must respond 200 with a navigation header."""
    for path in ("/", "/categories", "/parsing", "/history", "/settings"):
        r = await client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        # Every page includes the top nav with all five links
        body = r.text
        for nav_path, label in [
            ("/", "Машины"),
            ("/categories", "Категории"),
            ("/parsing", "Парсинг"),
            ("/history", "История"),
            ("/settings", "Настройки"),
        ]:
            assert f'href="{nav_path}"' in body, (
                f"{path} missing nav link to {nav_path}"
            )
            assert label in body, f"{path} missing nav label {label!r}"
