"""Integration tests for the FastAPI web viewer.

These tests build the app via :func:`encar_parser.web.app.create_app`,
seed an in-memory aiosqlite DB with a handful of cars, and exercise both
endpoints (``/`` and ``/img``). They do NOT touch the network — the
image proxy is mocked with respx.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car
from encar_parser.web.app import create_app


@pytest.fixture
async def session():
    """In-memory aiosqlite with the cars table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(Car(
            encar_id=42067452,
            brand="BMW", model="X5 (G05)",
            year_month=date(2023, 3, 1), mileage_km=59538, displacement_cc=2993,
            fuel_ru="Дизель", fuel_original="디젤",
            transmission_ru="Автомат", transmission_orig="오토",
            body_type="SUV",
            color_ru="Белый", color_original="흰색", seats=7,
            price_krw=78_900_000,
            photo_urls=["https://ci.encar.com/carpicture06/pic4206/42063010_042.jpg",
                        "https://ci.encar.com/carpicture06/pic4206/42063010_044.jpg"],
            encar_detail_url="https://fem.encar.com/cars/detail/42067452",
            last_seen_at=datetime.now(UTC),
        ))
        s.add(Car(
            encar_id=41411209,
            brand="BMW", model="X5 (G05)",
            year_month=date(2022, 2, 1), mileage_km=102561, displacement_cc=2998,
            fuel_ru="Бензин+Электро", fuel_original="가솔린+전기",
            transmission_ru="Автомат", transmission_orig="오토",
            body_type="SUV",
            color_ru="Серый", color_original="쥐색", seats=5,
            price_krw=55_000_000,
            photo_urls=["https://ci.encar.com/carpicture10/pic4140/41406494_003.jpg"],
            encar_detail_url="https://fem.encar.com/cars/detail/41411209",
            last_seen_at=datetime.now(UTC),
        ))
        await s.commit()
    yield Session
    await engine.dispose()


@pytest.fixture
async def client(session):
    """ASGI client backed by the FastAPI app, wired to the in-memory aiosqlite
    sessionmaker so the / route reads from the seeded DB."""
    app = create_app(sessionmaker=session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── GET / ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_renders_table(client):
    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "text/html" in r.headers["content-type"]
    assert 'charset="utf-8"' in body.lower()
    # Table headers we promised in the spec.
    for col in ("миниатюра", "brand", "model", "year_month", "mileage_km",
                "price_krw", "price_rub", "fuel_ru", "transmission_ru",
                "color_ru"):
        assert col in body, f"missing column header: {col}"
    # Both cars show up.
    assert "42067452" in body
    assert "41411209" in body


@pytest.mark.asyncio
async def test_index_shows_count_and_refresh_button(client):
    body = (await client.get("/")).text
    assert "обновлено" in body.lower()
    # Refresh = a form that GETs "/" + a button. Together they reload the page.
    assert 'action="/"' in body
    assert "Обновить" in body


@pytest.mark.asyncio
async def test_index_thumbnails_use_img_proxy(client):
    body = (await client.get("/")).text
    # Each car has exactly one <img src="/img?src=..."> for its thumbnail.
    assert body.count('<img src="/img?src=') == 2
    # The src must be urlencoded ci.encar.com URL.
    assert "ci.encar.com%2Fcarpicture06%2Fpic4206%2F42063010_042.jpg" in body
    # The thumbnail <img> must be wrapped in an <a> for click-to-open.
    # We check that the link href is the same /img?src=... (browser will
    # navigate to it and render the full-size JPEG in a new tab).
    assert body.count('<a href="/img?src=') >= 2
    assert 'target="_blank"' in body


@pytest.mark.asyncio
async def test_index_shows_placeholder_when_no_photos():
    """Cars with empty photo_urls must show 'нет фото' instead of a broken <img>."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(Car(
            encar_id=88888888,
            brand="BMW", model="X5 (G05)",
            year_month=date(2024, 1, 1), mileage_km=1000,
            fuel_ru="Бензин", fuel_original="가솔린",
            transmission_ru="Автомат", transmission_orig="오토",
            color_ru="Белый", color_original="흰색",
            price_krw=10_000_000,
            photo_urls=[],   # no photos
            encar_detail_url=None,
            last_seen_at=datetime.now(UTC),
        ))
        await s.commit()

    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/")).text

    assert "нет фото" in body
    # The car row should NOT have an <img> tag (placeholder, not a broken image).
    assert "<img" not in body.split("</thead>")[1]  # check only the tbody
    await engine.dispose()


@pytest.mark.asyncio
async def test_index_computes_price_rub(client):
    body = (await client.get("/")).text
    # 78_900_000 KRW * 0.048 = 3_787_200 ₽ — at least the order of magnitude
    # should be visible. We don't pin the exact value because the rate can
    # be changed via env without test changes.
    assert "3" in body and "787" in body  # 78_900_000 * 0.048


@pytest.mark.asyncio
async def test_index_links_to_encar(client):
    body = (await client.get("/")).text
    assert "fem.encar.com/cars/detail/42067452" in body


@pytest.mark.asyncio
async def test_index_korean_does_not_leak(client):
    """쥐색 (Korean grey) must translate, not appear raw in the table."""
    body = (await client.get("/")).text
    assert "쥐색" not in body
    assert "Серый" in body  # the translation we expect for the second car


@pytest.mark.asyncio
async def test_index_re_translates_stale_color_ru():
    """Regression: cars parsed before a Korean color was added to the dict
    carry the raw Korean in ``color_ru``. The web view must re-translate
    at render time so the table never shows raw Korean — even for cars
    whose ``color_ru`` column still holds the un-translated value.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(Car(
            encar_id=99999999,
            brand="BMW", model="X5 (G05)",
            year_month=date(2026, 1, 1), mileage_km=100,
            fuel_ru="Бензин", fuel_original="가솔린",
            transmission_ru="Автомат", transmission_orig="오토",
            color_ru="청색",          # stale: parser pass-through before dict had this
            color_original="청색",     # but original is the source of truth
            price_krw=50_000_000,
            photo_urls=[],
            encar_detail_url="https://fem.encar.com/cars/detail/99999999",
            last_seen_at=datetime.now(UTC),
        ))
        await s.commit()

    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/")).text

    assert "청색" not in body, "raw Korean must not appear in the table"
    assert "Синий" in body
    await engine.dispose()


# ── GET /img ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_img_proxies_real_url(client):
    src = "https://ci.encar.com/carpicture06/pic4206/42063010_042.jpg"
    with respx.mock(base_url="https://ci.encar.com") as mock:
        route = mock.get("/carpicture06/pic4206/42063010_042.jpg").mock(
            return_value=Response(200, content=b"FAKEJPEG",
                                  headers={"content-type": "image/jpeg"})
        )
        r = await client.get(f"/img?src={src}")
    assert r.status_code == 200
    assert r.content == b"FAKEJPEG"
    assert r.headers["content-type"] == "image/jpeg"
    assert route.called


@pytest.mark.asyncio
async def test_img_rejects_disallowed_host(client):
    r = await client.get("/img?src=https://example.com/whatever.jpg")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_img_swaps_img_to_ci(client):
    """Legacy img.encar.com URLs must transparently become ci.encar.com."""
    img_url = "https://img.encar.com/carpicture06/x/9_042.jpg"
    with respx.mock() as mock:
        ci_route = mock.get("https://ci.encar.com/carpicture06/x/9_042.jpg").mock(
            return_value=Response(200, content=b"OK",
                                  headers={"content-type": "image/jpeg"})
        )
        r = await client.get(f"/img?src={img_url}")
    assert r.status_code == 200
    assert ci_route.called


@pytest.mark.asyncio
async def test_img_404_when_upstream_missing(client):
    with respx.mock(base_url="https://ci.encar.com") as mock:
        mock.get("/missing.jpg").mock(return_value=Response(404))
        r = await client.get("/img?src=https://ci.encar.com/missing.jpg")
    assert r.status_code == 404


# ── dedup: vitrine filters out hidden duplicates ───────────────────────


@pytest.mark.asyncio
async def test_index_hides_cars_marked_not_primary():
    """A duplicate listing (is_primary=False) must not appear in the table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        # Primary listing — must appear in the vitrine.
        s.add(Car(
            encar_id=42213576, brand="BMW", model="X5 (G05)",
            year_month=date(2025, 12, 1), mileage_km=4645,
            color_ru="Синий", color_original="청색",
            price_krw=133_900_000,
            photo_urls=["https://ci.encar.com/x/a.jpg"],
            encar_detail_url="https://fem.encar.com/cars/detail/42213576",
            last_seen_at=datetime.now(UTC),
            is_primary=True,
        ))
        # Older duplicate — dedup marked this hidden, it must NOT appear.
        s.add(Car(
            encar_id=42209462, brand="BMW", model="X5 (G05)",
            year_month=date(2025, 12, 1), mileage_km=4645,
            color_ru="Синий", color_original="청색",
            price_krw=133_900_000,
            photo_urls=["https://ci.encar.com/x/a.jpg"],
            encar_detail_url="https://fem.encar.com/cars/detail/42209462",
            last_seen_at=datetime.now(UTC),
            is_primary=False,
        ))
        await s.commit()

    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/")).text

    # Primary row is shown, hidden duplicate is not.
    assert "42213576" in body
    # The hidden ID must not appear as a row entry — it can appear inside
    # photo URL paths (which encode the listing's original ID), so check
    # the more specific "details page link" rather than the bare number.
    assert "fem.encar.com/cars/detail/42209462" not in body
    assert "fem.encar.com/cars/detail/42213576" in body
    # The counter reflects unique cars (1), not raw rows (2).
    assert "Машин в БД (is_primary=true): <strong>1</strong>" in body
    await engine.dispose()


@pytest.mark.asyncio
async def test_index_count_excludes_hidden_duplicates():
    """Three rows: two duplicates hidden, one primary → counter shows 1."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        for eid, primary in [(100, True), (200, False), (300, False)]:
            s.add(Car(
                encar_id=eid, brand="BMW", model="X5 (G05)",
                year_month=date(2024, 1, 1), mileage_km=10000,
                color_ru="Белый", color_original="흰색",
                price_krw=50_000_000,
                photo_urls=[],
                encar_detail_url=None,
                last_seen_at=datetime.now(UTC),
                is_primary=primary,
            ))
        await s.commit()

    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/")).text

    assert "100" in body  # primary visible
    assert "200" not in body
    assert "300" not in body
    assert "Машин в БД (is_primary=true): <strong>1</strong>" in body
    await engine.dispose()
